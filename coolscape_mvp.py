"""
CoolScape AI – MVP modülleri (Streamlit'ten bağımsız, test edilebilir):
  1. Nokta yönetimi: OpenStreetMap parkları, belediye CSV'si, pilot parklarla birleştirme
  2. Gölge: ESA WorldCover ağaç örtüsü (yoksa Sentinel-2 NDVI vekili)
  3. Operatör planı: sade Türkçe, yazdırılabilir HTML
  4. Uygulama ve saha ölçümü kaydı: Google Sheets (yapılandırıldıysa) ya da yerel CSV
"""

import html
import io
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 1. NOKTA YÖNETİMİ
# ---------------------------------------------------------------------------
PARK_OSM_JSON = "parklar_osm.json"   # overpass-turbo "ham veri" dışa aktarımı (way/relation + geometry)
NOKTA_CSV = "noktalar.csv"           # belediyenin kendi nokta envanteri (varsa varsayılan nokta kümesi olur)
ERISIM_PARK_MIN_M2 = 5000            # VARSAYIM: erişim analizinde "serinletici park" sayılan en küçük alan (0,5 ha)
SULANAN_PAY = 0.5                    # TEMSİLİ: OSM park alanının sulanan (çim/peyzaj) kısmı
KAPASITE_HA = 10.0                   # TEMSİLİ: sulanan hektar başına hat kapasitesi (m³/saat), 5–30 arası
POMPA_VARSAYILAN = 1.2               # TEMSİLİ
PILOT_ESLESME_M = 250                # bu mesafedeki OSM parkı pilot parkla aynı sayılır

NOKTA_SUTUNLARI = ["ad", "tur", "lat", "lon", "sulanan_alan_m2", "kapasite_m3_saat", "su_kaynagi", "vana", "ilce"]
NOKTA_SABLONU = (
    "ad,tur,lat,lon,sulanan_alan_m2,kapasite_m3_saat,su_kaynagi,vana,ilce\n"
    "Dokuma Park,park,36.91285,30.67538,10000,10,içme suyu,V-DOK-01,Kepez\n"
    "Kalekapısı sisleme,sisleme,36.8869,30.7046,100,0.12,içme suyu,S-KLK-01,Muratpaşa\n"
    "Işıklar Caddesi,islatma,36.8846,30.7135,3000,5,arıtılmış su,ARAZOZ-3,Muratpaşa\n"
)
TUR_ESLEME = {"park": "park", "park sulama": "park", "sisleme": "sisleme", "sis": "sisleme",
              "islatma": "islatma", "ıslatma": "islatma", "yol ıslatma": "islatma", "yol islatma": "islatma"}


def _mesafe(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def _halka_alan_merkez(coords):
    """Basit eşdikdörtgen izdüşümde halka alanı (m²) ve ağırlık merkezi. Pilot bölge ölçeğinde hata < %1."""
    lat = np.array([c["lat"] for c in coords], dtype=float)
    lon = np.array([c["lon"] for c in coords], dtype=float)
    if len(lat) < 3:
        return 0.0, float(lat.mean()), float(lon.mean())
    k = np.cos(np.radians(lat.mean()))
    x, y = np.radians(lon) * 6371000 * k, np.radians(lat) * 6371000
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    capraz = x * y2 - x2 * y
    a = capraz.sum() / 2
    if abs(a) < 1e-6:
        return 0.0, float(lat.mean()), float(lon.mean())
    cx, cy = ((x + x2) * capraz).sum() / (6 * a), ((y + y2) * capraz).sum() / (6 * a)
    return abs(a), float(np.degrees(cy / 6371000)), float(np.degrees(cx / (6371000 * k)))


def osm_parklari_oku(yol=PARK_OSM_JSON):
    """Overpass JSON (out geom) → park, lat, lon, alan_m2, osm_id. Okunamazsa None."""
    if not os.path.exists(yol):
        return None
    try:
        elemanlar = json.load(open(yol, encoding="utf-8")).get("elements", [])
    except Exception:
        return None
    satirlar = []
    for e in elemanlar:
        t = e.get("tags", {})
        if e.get("type") == "way" and e.get("geometry"):
            alan, lat, lon = _halka_alan_merkez(e["geometry"])
        elif e.get("type") == "relation":
            parcalar = [_halka_alan_merkez(m["geometry"]) for m in e.get("members", [])
                        if m.get("role") == "outer" and m.get("geometry")]
            if not parcalar:
                continue
            alan = sum(p[0] for p in parcalar)
            lat = sum(p[0] * p[1] for p in parcalar) / max(alan, 1e-9)
            lon = sum(p[0] * p[2] for p in parcalar) / max(alan, 1e-9)
        else:
            continue
        if alan <= 0:
            continue
        satirlar.append({"park": t.get("name") or t.get("name:tr"), "lat": lat, "lon": lon,
                         "alan_m2": round(alan), "osm_id": f"{e['type'][0]}{e['id']}"})
    if not satirlar:
        return None
    df = pd.DataFrame(satirlar)
    df = df.sort_values("alan_m2", ascending=False).reset_index(drop=True)
    adsiz = df["park"].isna()
    ilce = pd.Series([_ilce_tahmini(a, b) for a, b in zip(df["lat"], df["lon"])], index=df.index)
    sira = ilce[adsiz].groupby(ilce[adsiz]).cumcount() + 1
    df.loc[adsiz, "park"] = "Adsız park · " + ilce[adsiz] + " " + sira.astype(str)
    tekrar = df["park"].duplicated(keep=False)
    df.loc[tekrar, "park"] = df.loc[tekrar, "park"] + " (" + df.loc[tekrar, "osm_id"] + ")"
    return df.sort_values("alan_m2", ascending=False).reset_index(drop=True)


def _ilce_tahmini(lat, lon):
    """Yönlendirme amaçlı kaba ilçe etiketi (sınır verisi yok)."""
    if lon < 30.665:
        return "Konyaaltı"
    return "Kepez" if lat > 36.905 else "Muratpaşa"


def osm_parklarini_noktaya_cevir(osm, pilot, min_alan_m2):
    """OSM parklarını uygulamanın nokta şemasına çevirir. Pilot parklara yakın OSM kayıtları atılır (pilot
    adı ve parametreleri korunur). Hidrolik parametreler TEMSİLİ'dir: belediye CSV'si ile değiştirilir."""
    o = osm[osm["alan_m2"] >= min_alan_m2].copy()
    if pilot is not None and len(pilot):
        yakin = np.array([np.min(_mesafe(r["lat"], r["lon"], pilot["lat"].values, pilot["lon"].values))
                          for _, r in o.iterrows()]) if len(o) else np.array([])
        o = o[yakin > PILOT_ESLESME_M] if len(o) else o
    sulanan = o["alan_m2"] * SULANAN_PAY
    return pd.DataFrame({
        "park": o["park"].values, "ilce": [_ilce_tahmini(a, b) for a, b in zip(o["lat"], o["lon"])],
        "lat": o["lat"].values, "lon": o["lon"].values, "yuzey_farki_c": 0.0, "yesil_skoru": 5.0,
        "nufus_skoru": 5.0, "hassas_yuzde": 15.0, "pompa_katsayisi": POMPA_VARSAYILAN,
        "hat_kapasitesi_m3_saat": np.clip(KAPASITE_HA * sulanan.values / 10_000, 5, 30).round(1),
        "sulanan_alan_ha": (sulanan.values / 10_000).round(2), "aritilmis_su_erisimi": False, "tur": "park",
        "su_kaynagi": "bilinmiyor", "vana": "", "alan_m2": o["alan_m2"].values,
        "veri_kaynagi": "OSM konumu ve alanı; hidrolik değerler temsili"})


def nokta_csv_oku(dosya):
    """Belediye nokta CSV'si → (nokta DataFrame'i uygulama şemasında, uyarılar). Hatalı satırlar atlanır."""
    uyarilar = []
    try:
        df = pd.read_csv(dosya, sep=None, engine="python", encoding="utf-8-sig")
    except Exception as hata:
        return None, [f"CSV okunamadı: {hata}"]
    df.columns = [str(c).strip().lower() for c in df.columns]
    eksik = {"ad", "tur", "lat", "lon"} - set(df.columns)
    if eksik:
        return None, [f"Zorunlu sütunlar eksik: {', '.join(sorted(eksik))}"]
    for c in NOKTA_SUTUNLARI:
        if c not in df:
            df[c] = np.nan
    df["tur"] = df["tur"].astype(str).str.strip().str.lower().map(TUR_ESLEME)
    df["lat"] = pd.to_numeric(df["lat"].astype(str).str.replace(",", "."), errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"].astype(str).str.replace(",", "."), errors="coerce")
    gecersiz = df["tur"].isna() | df["lat"].isna() | df["lon"].isna() | df["ad"].isna()
    if gecersiz.any():
        uyarilar.append(f"{int(gecersiz.sum())} satır atlandı (ad, tür, enlem veya boylam eksik/geçersiz; "
                        "tür: park, sisleme, islatma).")
    df = df[~gecersiz].copy()
    disari = ~df["lat"].between(36.80, 37.02) | ~df["lon"].between(30.55, 30.85)
    if disari.any():
        uyarilar.append(f"{int(disari.sum())} nokta pilot bölgenin dışında; ısı yüzeyi verisi orada yok, atlandı.")
        df = df[~disari]
    if df.empty:
        return None, uyarilar + ["Geçerli nokta kalmadı."]
    from_sayi = lambda s: pd.to_numeric(s.astype(str).str.replace(",", "."), errors="coerce")
    alan = from_sayi(df["sulanan_alan_m2"])
    kap = from_sayi(df["kapasite_m3_saat"])
    varsayilan_alan = df["tur"].map({"park": 10_000, "sisleme": 100, "islatma": 3000})
    varsayilan_kap = df["tur"].map({"park": 10.0, "sisleme": 0.12, "islatma": 5.0})
    temsili = alan.isna() | kap.isna()
    if temsili.any():
        uyarilar.append(f"{int(temsili.sum())} noktada alan veya kapasite boş; türün temsili değeri kullanıldı.")
    kaynak = df["su_kaynagi"].fillna("bilinmiyor").astype(str).str.strip()
    out = pd.DataFrame({
        "park": df["ad"].astype(str).str.strip().values,
        "ilce": [i if isinstance(i, str) and i.strip() else _ilce_tahmini(a, b)
                 for i, a, b in zip(df["ilce"], df["lat"], df["lon"])],
        "lat": df["lat"].values, "lon": df["lon"].values, "yuzey_farki_c": 0.0, "yesil_skoru": 5.0,
        "nufus_skoru": 5.0, "hassas_yuzde": 15.0, "pompa_katsayisi": POMPA_VARSAYILAN,
        "hat_kapasitesi_m3_saat": kap.fillna(varsayilan_kap).values.astype(float),
        "sulanan_alan_ha": (alan.fillna(varsayilan_alan) / 10_000).values.astype(float),
        "aritilmis_su_erisimi": kaynak.str.lower().str.contains("arıtılmış|aritilmis|gri").values,
        "tur": df["tur"].values, "su_kaynagi": kaynak.values,
        "vana": df["vana"].fillna("").astype(str).values,
        "veri_kaynagi": np.where(temsili.values, "Belediye CSV (eksik alanlar temsili)", "Belediye CSV")})
    tekrar = out["park"].duplicated(keep=False)
    if tekrar.any():
        out.loc[tekrar, "park"] = out.loc[tekrar, "park"] + " #" + (out[tekrar].groupby("park").cumcount() + 1).astype(str)
        uyarilar.append("Aynı adlı noktalar numaralandırıldı.")
    return out.reset_index(drop=True), uyarilar


def park_secim_skoru(parklar, yuzey, yaricap_m=800):
    """Planlamaya alınacak parkları sıralamak için: çevredeki (800 m) nüfus × ısı çarpanı.
    Nüfus yoksa okul/sağlık yoğunluğu, o da yoksa park alanı kullanılır."""
    if yuzey is None:
        return parklar.get("alan_m2", pd.Series(1.0, index=parklar.index)).astype(float)
    lat, lon = yuzey["lat"].values, yuzey["lon"].values
    olcu = "nufus" if "nufus" in yuzey else ("n_tesis_400" if "n_tesis_400" in yuzey else None)
    skor = []
    for _, r in parklar.iterrows():
        ic = _mesafe(r["lat"], r["lon"], lat, lon) <= yaricap_m
        n = float(yuzey.loc[ic, olcu].sum()) if olcu else 1.0
        isi = float(yuzey.loc[ic, "lst_fark_c"].mean()) if ic.any() else 0.0
        skor.append(n * max(0.2, 1 + isi / 5))
    return pd.Series(skor, index=parklar.index)


# ---------------------------------------------------------------------------
# 2. GÖLGE (ağaç örtüsü)
# ---------------------------------------------------------------------------
GOLGE_CSV = "golge_h3.csv"
NDVI_TOPRAK, NDVI_BITKI = 0.2, 0.8   # Carlson ve Ripley (1997) bitki örtüsü oranı: ((NDVI−0,2)/(0,8−0,2))²


def golge_oku(yol=GOLGE_CSV):
    if not os.path.exists(yol):
        return None
    try:
        df = pd.read_csv(yol)
    except Exception:
        return None
    if not {"hex", "agac_orani"} <= set(df.columns):
        return None
    return df[["hex", "agac_orani"] + [c for c in ("yapi_orani", "cim_orani") if c in df.columns]]


def golge_ekle(yuzey, golge):
    """yuzey'e 'golge' (0–1, yayaların ağaç gölgesindeki payı) ve kaynağını ekler.
    Öncelik: WorldCover ağaç örtüsü; yoksa NDVI'dan bitki örtüsü oranı (çimi de sayar: üst sınır vekili); yoksa 0."""
    y = yuzey.copy()
    if golge is not None:
        y = y.drop(columns=[c for c in golge.columns if c != "hex" and c in y]).merge(golge, on="hex", how="left")
        y["golge"] = y["agac_orani"].fillna(0.0).clip(0, 1)
        y.attrs["golge_kaynak"] = (f"ESA WorldCover 2021 ağaç örtüsü ({int(y['agac_orani'].notna().sum())} altıgen, "
                                   f"ortalama %{100 * y['golge'].mean():.0f})")
    elif "ndvi" in y and y["ndvi"].notna().any():
        fvc = ((y["ndvi"] - NDVI_TOPRAK) / (NDVI_BITKI - NDVI_TOPRAK)).clip(0, 1) ** 2
        y["golge"] = fvc.fillna(0.0)
        y.attrs["golge_kaynak"] = (f"Sentinel-2 NDVI bitki örtüsü oranı (vekil; yalnızca {int(y['ndvi'].notna().sum())} "
                                   "altıgende veri var, kalanında gölge 0)")
    else:
        y["golge"] = 0.0
        y.attrs["golge_kaynak"] = "gölge verisi yok (herkes güneşte kabul edildi)"
    return y


def nokta_golgesi(noktalar, yuzey, h3mod, park_halka=1):
    """Noktanın gölge payı: park için çevresindeki altıgenler (park alanı), diğerleri için kendi altıgeni."""
    p = noktalar.copy()
    p["golge"] = 0.0
    if yuzey is None or "golge" not in yuzey:
        return p
    coz = h3mod.get_resolution(yuzey["hex"].iloc[0])
    g = yuzey.set_index("hex")["golge"]
    for i, r in p.iterrows():
        merkez = h3mod.latlng_to_cell(r["lat"], r["lon"], coz)
        komsu = h3mod.grid_disk(merkez, park_halka if r["tur"] == "park" else 0)
        degerler = [g[c] for c in komsu if c in g.index]
        p.at[i, "golge"] = round(float(np.mean(degerler)), 3) if degerler else 0.0
    return p


# ---------------------------------------------------------------------------
# 3. OPERATÖR PLANI (yazdırılabilir HTML)
# ---------------------------------------------------------------------------
def operator_plani_html(komut, gun_etiketi, tarih, butce, hava_ozet, notlar, uretim_zamani=None):
    """komut: Nokta, Yöntem, Başlangıç, Bitiş, Su (m³), Kaynak, Vana sütunlu tablo.
    Kâğıda basılıp sahada doldurulacak şekilde: her satırda 'Yapıldı' ve 'Uygulanan m³' boşlukları."""
    e = lambda s: html.escape(str(s))
    uretim = uretim_zamani or datetime.now().strftime("%d.%m.%Y %H:%M")
    satirlar = []
    for _, r in komut.iterrows():
        satirlar.append(
            "<tr>" + "".join(f"<td>{e(r.get(c, ''))}</td>" for c in ("Başlangıç", "Bitiş", "Nokta", "Yöntem", "Vana"))
            + f"<td class='sag'>{float(r['Su (m³)']):.2f}".replace(".", ",") + "</td>"
            + f"<td>{e(r.get('Kaynak', ''))}</td><td class='kutu'>☐</td><td class='bos'></td></tr>")
    toplam = float(komut["Su (m³)"].sum()) if len(komut) else 0.0
    gövde = ("".join(satirlar) if satirlar else
             "<tr><td colspan='9'>Bu gün için soğutma sulaması önerilmiyor (güçlü ısı stresi beklenmiyor).</td></tr>")
    not_html = "".join(f"<li>{e(n)}</li>" for n in notlar)
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<title>CoolScape günlük su planı {e(tarih)}</title>
<style>
 body{{font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#111;margin:18px}}
 h1{{font-size:18px;margin:0 0 2px}} .alt{{color:#444;margin-bottom:10px}}
 table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #999;padding:5px 6px;vertical-align:top}}
 th{{background:#eee;text-align:left}} .sag{{text-align:right}} .kutu{{text-align:center;font-size:15px}}
 .bos{{min-width:70px}} .ozet td{{border:none;padding:2px 10px 2px 0}} ul{{margin:6px 0 0 16px;padding:0}}
 .imza td{{border:none;padding-top:22px}} @media print{{body{{margin:8mm}} button{{display:none}}}}
</style></head><body>
<button onclick="window.print()">Yazdır</button>
<h1>CoolScape AI · Günlük soğutma suyu planı</h1>
<div class="alt">{e(gun_etiketi)} · {e(tarih)} · Antalya Muratpaşa–Kepez · üretildi {e(uretim)}</div>
<table class="ozet"><tr><td><b>Su bütçesi</b> {butce:.0f} m³</td><td><b>Planlanan</b> {f"{toplam:.1f}".replace(".", ",")} m³</td>
<td>{e(hava_ozet)}</td></tr></table><br>
<table><thead><tr><th>Başlangıç</th><th>Bitiş</th><th>Nokta</th><th>Yöntem</th><th>Vana / araç</th>
<th>Su (m³)</th><th>Su kaynağı</th><th>Yapıldı</th><th>Uygulanan m³</th></tr></thead>
<tbody>{gövde}</tbody></table>
<ul>{not_html}</ul>
<table class="imza"><tr><td>Uygulayan: ______________________</td><td>Onaylayan: ______________________</td>
<td>Tarih/saat: ________________</td></tr></table>
</body></html>"""


# ---------------------------------------------------------------------------
# 4. UYGULAMA VE SAHA ÖLÇÜMÜ KAYDI
# ---------------------------------------------------------------------------
KAYIT_SUTUNLARI = ["kayit_zamani", "tarih", "nokta", "yontem", "baslangic", "bitis", "planlanan_m3", "uygulanan_m3",
                   "su_kaynagi", "olcum_saati", "t_once_c", "t_sonra_c", "rh_once", "rh_sonra", "kure_once_c",
                   "kure_sonra_c", "ruzgar_ms", "operator", "not"]
KAYIT_CSV = "kayitlar.csv"
KURE_CAPI_M, KURE_EMISIVITE = 0.15, 0.95   # standart 150 mm siyah küre termometresi (ISO 7726)


def kure_mrt(tg, ta, v, d=KURE_CAPI_M, eps=KURE_EMISIVITE):
    """Küre termometresinden MRT (ISO 7726, zorlanmış taşınım): ((Tg+273)^4 + 1,1e8·v^0,6/(ε·D^0,4)·(Tg−Ta))^0,25 − 273."""
    tg, ta, v = (np.asarray(x, dtype=float) for x in (tg, ta, v))
    ic = (tg + 273.15) ** 4 + 1.1e8 * np.maximum(v, 0.1) ** 0.6 / (eps * d ** 0.4) * (tg - ta)
    return np.maximum(ic, 0) ** 0.25 - 273.15


class KayitDeposu:
    """Google Sheets yapılandırıldıysa (st.secrets: gcp_service_account + kayit_sheet_id) kalıcı kayıt;
    yoksa uygulama klasöründe CSV (Streamlit Cloud'da yeniden başlatmada silinir: indirip saklayın)."""

    def __init__(self, gizli=None, yol=KAYIT_CSV):
        self.yol = yol
        self.ws = None
        self.hata = None
        gizli = gizli or {}
        if "gcp_service_account" in gizli and "kayit_sheet_id" in gizli:
            try:
                import gspread
                gc = gspread.service_account_from_dict(dict(gizli["gcp_service_account"]))
                sh = gc.open_by_key(gizli["kayit_sheet_id"])
                try:
                    self.ws = sh.worksheet("kayitlar")
                except Exception:
                    self.ws = sh.add_worksheet("kayitlar", rows=1000, cols=len(KAYIT_SUTUNLARI))
                    self.ws.append_row(KAYIT_SUTUNLARI)
            except Exception as hata:
                self.ws, self.hata = None, f"{type(hata).__name__}: {hata}"

    @property
    def kalici(self):
        return self.ws is not None

    @property
    def tur(self):
        return "Google Sheets (kalıcı)" if self.kalici else "geçici CSV (uygulama yeniden başlayınca silinir)"

    def oku(self):
        if self.kalici:
            kayitlar = self.ws.get_all_records()
            df = pd.DataFrame(kayitlar)
        elif os.path.exists(self.yol):
            df = pd.read_csv(self.yol)
        else:
            df = pd.DataFrame()
        for c in KAYIT_SUTUNLARI:
            if c not in df:
                df[c] = np.nan
        return df[KAYIT_SUTUNLARI]

    def ekle(self, satirlar):
        df = pd.DataFrame(satirlar)
        for c in KAYIT_SUTUNLARI:
            if c not in df:
                df[c] = ""
        df = df[KAYIT_SUTUNLARI]
        if self.kalici:
            self.ws.append_rows(df.astype(object).where(df.notna(), "").values.tolist(), value_input_option="RAW")
        else:
            ilk = not os.path.exists(self.yol)
            df.to_csv(self.yol, mode="a", header=ilk, index=False)
        return len(df)

    def ice_aktar(self, dosya):
        """Daha önce indirilen kayıt CSV'sini geri yükler (geçici depoda kaybolan kayıtlar için)."""
        df = pd.read_csv(dosya)
        eksik = {"tarih", "nokta", "uygulanan_m3"} - set(df.columns)
        if eksik:
            raise ValueError(f"Kayıt dosyasında eksik sütunlar: {', '.join(sorted(eksik))}")
        mevcut = self.oku()
        anahtar = ["kayit_zamani", "nokta", "tarih", "baslangic"]
        yeni = df.merge(mevcut[anahtar].astype(str), on=anahtar, how="left", indicator=True) \
            if set(anahtar) <= set(df.columns) else df.assign(_merge="left_only")
        yeni = yeni[yeni["_merge"] == "left_only"].drop(columns="_merge")
        return self.ekle(yeni.to_dict("records")) if len(yeni) else 0


def olcum_ozeti(kayitlar, utci_fn):
    """Önce/sonra ölçümü olan kayıtlar için ölçülen UTCI düşüşü. utci_fn(tdb, mrt, v, rh) → UTCI."""
    k = kayitlar.copy()
    sayi = ["t_once_c", "t_sonra_c", "rh_once", "rh_sonra", "kure_once_c", "kure_sonra_c", "ruzgar_ms"]
    for c in sayi:
        k[c] = pd.to_numeric(k[c], errors="coerce")
    tam = k.dropna(subset=["t_once_c", "t_sonra_c", "rh_once", "rh_sonra", "kure_once_c", "kure_sonra_c"]).copy()
    if tam.empty:
        return tam
    v = tam["ruzgar_ms"].fillna(1.0)
    tam["mrt_once_c"] = kure_mrt(tam["kure_once_c"], tam["t_once_c"], v)
    tam["mrt_sonra_c"] = kure_mrt(tam["kure_sonra_c"], tam["t_sonra_c"], v)
    tam["utci_once_c"] = utci_fn(tam["t_once_c"], tam["mrt_once_c"], v, tam["rh_once"])
    tam["utci_sonra_c"] = utci_fn(tam["t_sonra_c"], tam["mrt_sonra_c"], v, tam["rh_sonra"])
    tam["olculen_dusus_c"] = tam["utci_once_c"] - tam["utci_sonra_c"]
    return tam


def kayit_sablonu_csv():
    buf = io.StringIO()
    pd.DataFrame(columns=KAYIT_SUTUNLARI).to_csv(buf, index=False)
    return buf.getvalue()
