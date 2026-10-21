"""
CoolScape AI – su kaynağı ve yatırım hesabı (Streamlit'ten bağımsız, test edilebilir).

Soru: Parklar bugün hangi suyla sulanıyor, bunun maliyeti ne; arıtılmış suya geçilirse ne kadar içme suyu ve para
tasarruf edilir, bunun için hangi boru hattı ve yatırım gerekir?

Kurallar: her girdi ya bir kaynağa dayanır ya da ekranda "varsayım / belediyeden alınmalı" diye etiketlenir.
Birim fiyatı bilinmeyen kalemler 0 bırakılır ve sonuç "alt sınır" olarak raporlanır; uydurma fiyat yoktur.
"""

import json
import os

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# KAYNAKLI VARSAYILANLAR
# ---------------------------------------------------------------------------
KC_CIM = {"Serin iklim çimi (yaz)": 0.89, "Sıcak iklim çimi (yaz)": 0.70}
KC_KAYNAK = "UC ANR, Meyer vd. 1985: serin iklim çimi Haziran–Ağustos ort. ≈0,89; sıcak iklim çimi ≈0,70"
ETKILI_YAGIS = 0.8          # FAO yaklaşımı: aylık yağışın ~%80'i etkili kabul edilir (yaz yağışı Antalya'da çok az)
SULAMA_VERIMI = 0.75        # VARSAYIM: yağmurlama sistemi uygulama verimi (damla ~0,90)
SEZON = (6, 9)              # Haziran–Eylül
SULAMA_SAATI = 8.0          # VARSAYIM: gece sulama penceresi (saat); boru çapı bu sürede pik günü taşıyacak şekilde
TASARIM_HIZI = 1.2          # m/s, VARSAYIM: basınçlı PE hatlarda tipik tasarım hızı
DN_LISTESI = [63, 90, 110, 125, 160, 200, 250, 315, 400, 500, 630]   # PE100 dış çapları (mm), SDR17 iç çap ≈ 0,88·DN
KIVRIM = 1.3                # VARSAYIM: kentsel güzergâhın kuş uçuşu ağaca oranı
POMPA_YUKSEKLIGI_M = 40.0   # VARSAYIM: toplam manometrik yükseklik
POMPA_VERIMI = 0.70         # VARSAYIM

SU_BIRIM_VARSAYILAN = 34.0  # TL/m³, ASAT ticarethane/inşaat ilk kademe, Temmuz 2026 (KDV, atıksu ve ÇTV hariç)
SU_BIRIM_KAYNAK = ("ASAT Temmuz 2026 tarifesi, ticarethane ilk kademe 34 TL/m³ (KDV ve atıksu hariç; "
                   "antalyakenthaber.com, 15.07.2026). Parkların gerçek birim maliyeti belediyeden alınmalı.")
BORU_BIRIM_VARSAYILAN = 287.03  # TL/m, ÇŞB poz 25.305.7107/C, PE100 SDR17 PN10 Ø110, Aralık 2025
BORU_BIRIM_KAYNAK = ("ÇŞB birim fiyat poz 25.305.7107/C: PE100 SDR17 PN10 Ø110 boru temini ve montajı, Aralık 2025 "
                     "(287,03 TL/m). Daha büyük çap, kazı, dolgu ve yol onarımı dahil değildir: yatırım alt sınırdır.")

MEVZUAT_SINIF_A = pd.DataFrame([
    ["Kapsam", "Kentsel alanlar, parklar, golf sahaları (halkın eriştiği alanlar)"],
    ["Gerekli arıtma", "İkincil (biyolojik) arıtma + filtrasyon + dezenfeksiyon"],
    ["pH", "6–9"],
    ["BOİ₅", "< 20 mg/L"],
    ["Bulanıklık", "< 2 NTU (hiçbir zaman 5 NTU'yu aşmamalı)"],
    ["Fekal koliform", "0 /100 mL (hiçbir zaman 14 /100 mL'yi aşmamalı)"],
    ["Bakiye klor", "> 1 mg/L (dağıtım sisteminin son noktasında ≥ 0,5 mg/L)"],
    ["İzleme", "pH, BOİ₅ haftalık; bulanıklık ve klor sürekli; koliform günlük"],
    ["Mesafe", "İçme suyu kuyularına en az 50 m"],
], columns=["Ölçüt", "Sınıf A koşulu"])
MEVZUAT_NOTLARI = [
    "Kaynak: Atıksu Arıtma Tesisleri Teknik Usuller Tebliği (2010), Ek-7 Tablo E7.1 Sınıf A; değerler Haliç Çevre "
    "Laboratuvarı özetinden alınmıştır, uygulamadan önce Resmî Gazete metniyle doğrulanmalıdır.",
    "25.10.2022 tarihli değişiklik (RG 31994): yeniden kullanım alanları genişletildi; yeniden kullanım için Çevre, "
    "Şehircilik ve İklim Değişikliği Bakanlığı'nın uygun görüşü alınır (md. 18); kullanım verileri atıksu bilgi "
    "sistemine girilir (md. 22/D); döngüsel ekonomi kapsamında teşvik edilir (md. 22/Ç).",
    "Ulusal hedef: arıtılmış atıksuyun yeniden kullanım oranı 2030'da %15 (Bakanlık açıklaması, Ekim 2022).",
    "CoolScape kuralı: sisleme her zaman içme suyuyla yapılır (aerosol ve lejyonella riski); arıtılmış su yalnızca "
    "park sulaması ve yol ıslatmada kullanılır.",
]
FINANSMAN = pd.DataFrame([
    ["AFD (Fransız Kalkınma Ajansı)", "Antalya Büyükşehir'e 59,85 M€ kredi + 0,3 M€ hibe; 15 yıl vade, 4 yıl ödemesiz, "
     "Hazine garantisiz; Hurma ve Lara atıksu tesisleri projeleri (2023)", "Antalya'nın mevcut kreditörü; "
     "Lara AAT kapasite artışı da AFD desteğiyle sürüyor"],
    ["Dünya Bankası", "Türkiye Water Circularity and Efficiency Improvement Project (P174915): 434,65 M USD, "
     "Mayıs 2023; uygulayıcılar DSİ ve İLBANK", "Su döngüselliği başlıklı, etkin bir program"],
    ["Avrupa Yatırım Bankası (AYB/EIB)", "İLBANK aracılığıyla belediye su, atıksu ve katı atık yatırımlarına "
     "250 M€ (2015)", "İLBANK üzerinden belediye kredisi modeli"],
    ["EBRD (Avrupa İmar ve Kalkınma Bankası)", "Osmaniye yeni AAT için 42 M€ egemen kredi (Şubat 2026), İLBANK "
     "uygulayıcı; Green Cities programı (İstanbul eylem planı)", "Atıksu ve kentsel iklim uyumu finansmanı"],
    ["İLBANK (İller Bankası)", "Yukarıdaki uluslararası kredilerin yerel aracısı; belediye yatırım kredisi",
     "Belediyenin doğrudan başvuracağı kurum"],
], columns=["Kurum", "Örnek finansman", "Neden ilgili"])
FINANSMAN_KAYNAK = ("Kaynaklar: Ekonomi Gazetesi (AFD–Antalya, 14.11.2023); Yeni Birlik (Lara AAT, 2026); "
                    "Dünya Bankası proje veritabanı P174915; AYB basın bülteni 2015-219; EBRD haberi (20.02.2026).")


# ---------------------------------------------------------------------------
# ARITMA TESİSLERİ (OSM)
# ---------------------------------------------------------------------------
AAT_OSM_JSON = "aat_osm.json"


def aat_oku(yol=AAT_OSM_JSON):
    """Overpass JSON (man_made=wastewater_plant, out center) → ad, lat, lon. Yoksa None."""
    if not os.path.exists(yol):
        return None
    try:
        veri = json.load(open(yol, encoding="utf-8"))
    except Exception:
        return None
    return aat_cozumle(veri)


AAT_SORGU = ('[out:json][timeout:60];nwr["man_made"="wastewater_plant"](36.70,30.40,37.10,31.00);out center tags;')
OVERPASS_SUNUCULARI = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
                       "https://overpass.private.coffee/api/interpreter"]


def aat_overpass_cek(oturum=None, zaman_asimi=40):
    """Arıtma tesislerini OpenStreetMap'ten (Overpass) canlı çeker; sunucular sırayla denenir. (DataFrame, ham) ya da (None, hata)."""
    import requests
    oturum = oturum or requests
    son = "?"
    for url in OVERPASS_SUNUCULARI:
        try:
            r = oturum.post(url, data={"data": AAT_SORGU}, timeout=zaman_asimi,
                            headers={"User-Agent": "CoolScape-AI/1.0 (Climathon prototipi)"})
            r.raise_for_status()
            veri = r.json()
            df = aat_cozumle(veri)
            if df is not None:
                return df, veri
            son = "sonuç boş"
        except Exception as hata:
            son = f"{type(hata).__name__}"
    return None, son


def aat_cozumle(veri):
    el = veri.get("elements", []) if isinstance(veri, dict) else []
    s = []
    for e in el:
        lat = e.get("lat", e.get("center", {}).get("lat"))
        lon = e.get("lon", e.get("center", {}).get("lon"))
        if lat is None or lon is None:
            continue
        t = e.get("tags", {})
        s.append({"ad": t.get("name") or t.get("name:tr") or f"Arıtma tesisi (OSM {e['type'][0]}{e['id']})",
                  "lat": float(lat), "lon": float(lon), "operator": t.get("operator", "")})
    if not s:
        return None
    df = pd.DataFrame(s).drop_duplicates("ad").reset_index(drop=True)
    # Otel ve site paket tesisleri park sulamasına kaynak olamaz: ASAT ya da kent tesisi olduğu anlaşılanlar tercih edilir
    kent = df["operator"].str.contains("ASAT|Su ve Atıksu|Büyükşehir", case=False, na=False) | \
        df["ad"].str.contains("ASAT|Hurma|Lara|Kepez|Döşemealtı|Aksu|Antalya", case=False, na=False)
    df["kullanilan"] = kent if kent.any() else True
    return df


# ---------------------------------------------------------------------------
# HESAPLAR
# ---------------------------------------------------------------------------
def _mesafe(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def sezon_iklimi(gunluk):
    """gunluk: tarih, et0_mm, yagis_mm (Open-Meteo günlük). Sezon toplamları ve pik gün."""
    g = gunluk.copy()
    g["ay"] = pd.to_datetime(g["tarih"]).dt.month
    g = g[g["ay"].between(*SEZON)]
    return {"gun": len(g), "et0_toplam": float(g["et0_mm"].sum()), "yagis_toplam": float(g["yagis_mm"].sum()),
            "et0_pik": float(g["et0_mm"].max()) if len(g) else 0.0}


def sulama_ihtiyaci(alan_m2, iklim, kc, verim=SULAMA_VERIMI):
    """Sezonluk ve pik gün brüt sulama ihtiyacı (m³). Net = max(Kc·ΣET₀ − 0,8·ΣP, 0); brüt = net / verim."""
    net_mm = max(kc * iklim["et0_toplam"] - ETKILI_YAGIS * iklim["yagis_toplam"], 0.0)
    sezon_m3 = alan_m2 * net_mm / 1000.0 / verim
    pik_m3 = alan_m2 * kc * iklim["et0_pik"] / 1000.0 / verim
    return {"net_mm": net_mm, "sezon_m3": sezon_m3, "pik_gun_m3": pik_m3}


def en_kucuk_agac_uzunlugu(noktalar):
    """Noktalar arası en kısa bağlantı ağacı (Prim), metre. noktalar: lat, lon sütunlu."""
    lat, lon = noktalar["lat"].values, noktalar["lon"].values
    n = len(lat)
    if n < 2:
        return 0.0, []
    d = _mesafe(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
    agacta = np.zeros(n, bool)
    agacta[0] = True
    en_iyi = d[0].copy()
    ebeveyn = np.zeros(n, int)
    toplam, kenarlar = 0.0, []
    for _ in range(n - 1):
        aday = np.where(~agacta, en_iyi, np.inf)
        j = int(np.argmin(aday))
        toplam += float(aday[j])
        kenarlar.append((int(ebeveyn[j]), j))
        agacta[j] = True
        daha_iyi = d[j] < en_iyi
        en_iyi = np.where(daha_iyi, d[j], en_iyi)
        ebeveyn = np.where(daha_iyi, j, ebeveyn)
    return toplam, kenarlar


def cap_sec(q_m3s, hiz=TASARIM_HIZI):
    """Debiyi tasarım hızında taşıyan en küçük PE100 SDR17 çapı. Sığmazsa (DN, paralel hat sayısı)."""
    d_ic = np.sqrt(4 * q_m3s / (np.pi * hiz)) * 1000 if q_m3s > 0 else 0.0
    for x in DN_LISTESI:
        if 0.88 * x >= d_ic:
            return x, 1, d_ic
    en_buyuk = DN_LISTESI[-1]
    return en_buyuk, int(np.ceil((d_ic / (0.88 * en_buyuk)) ** 2)), d_ic


def boru_hatti(aat, parklar, pik_gun_m3, kivrim=KIVRIM, hiz=TASARIM_HIZI, sulama_saati=SULAMA_SAATI):
    """En yakın arıtma tesisinden parklara dağıtım ağı: her tesis kendi en yakın parklarına en kısa bağlantı ağacıyla
    bağlanır; uzunluk × kıvrım katsayısı. Ana hat çapı pik günün suyunu gece penceresinde taşıyacak şekilde seçilir."""
    if aat is None or aat.empty or parklar.empty:
        return None
    d = _mesafe(parklar["lat"].values[:, None], parklar["lon"].values[:, None],
                aat["lat"].values[None, :], aat["lon"].values[None, :])
    en_yakin = d.argmin(axis=1)
    agirlik = parklar["sulanan_m2"].values if "sulanan_m2" in parklar else np.ones(len(parklar))
    agirlik = agirlik / max(agirlik.sum(), 1e-9)
    uzunluk, kenar_listesi, tesis_ozet = 0.0, [], []
    for k in np.unique(en_yakin):
        grup = pd.concat([aat.iloc[[k]][["lat", "lon"]], parklar[en_yakin == k][["lat", "lon"]]], ignore_index=True)
        L, kenarlar = en_kucuk_agac_uzunlugu(grup)
        uzunluk += L
        kenar_listesi += [(grup.iloc[a][["lat", "lon"]].tolist(), grup.iloc[b][["lat", "lon"]].tolist()) for a, b in kenarlar]
        q_k = pik_gun_m3 * float(agirlik[en_yakin == k].sum()) / (sulama_saati * 3600.0)   # m³/s
        dn, paralel, _ = cap_sec(q_k, hiz)
        tesis_ozet.append({"Arıtma tesisi": aat.iloc[k]["ad"], "Bağlanan park": int((en_yakin == k).sum()),
                           "Ağ uzunluğu (km)": round(L * kivrim / 1000, 1),
                           "En uzak park (km)": round(float(d[en_yakin == k, k].max()) / 1000, 1),
                           "Pik debi (L/s)": round(q_k * 1000, 1),
                           "Ana hat (PE100)": f"DN{dn}" + (f" × {paralel} paralel" if paralel > 1 else "")})
    q = pik_gun_m3 / (sulama_saati * 3600.0)
    return {"uzunluk_m": uzunluk * kivrim, "kus_ucusu_m": uzunluk, "debi_ls": q * 1000,
            "kenarlar": kenar_listesi, "tesisler": pd.DataFrame(tesis_ozet), "park_aat": en_yakin}


def pompa_enerjisi_kwh(hacim_m3, yukseklik=POMPA_YUKSEKLIGI_M, verim=POMPA_VERIMI):
    return 1000 * 9.81 * hacim_m3 * yukseklik / (verim * 3.6e6)


def yatirim_analizi(sezon_m3, sebeke_payi, su_birim, boru, boru_birim, kazi_birim, ileri_aritma_yatirim,
                    ileri_aritma_birim, elektrik_birim):
    """Tüm TL değerleri girdilerden; bilinmeyen kalem 0 ise 'alt sınır' bayrağı döner."""
    mevcut_maliyet = sezon_m3 * su_birim
    icme_tasarruf_m3 = sezon_m3 * sebeke_payi
    kwh = pompa_enerjisi_kwh(sezon_m3)
    isletme = sezon_m3 * ileri_aritma_birim + kwh * elektrik_birim
    tasarruf_tl = mevcut_maliyet - isletme
    uzunluk = boru["uzunluk_m"] if boru else 0.0
    yatirim = uzunluk * (boru_birim + kazi_birim) + ileri_aritma_yatirim
    eksik = [ad for ad, v in (("kazı, dolgu ve yol onarımı", kazi_birim), ("ileri arıtma yatırımı", ileri_aritma_yatirim),
                              ("ileri arıtma işletme maliyeti", ileri_aritma_birim), ("elektrik birim fiyatı", elektrik_birim))
             if v == 0]
    return {"mevcut_maliyet": mevcut_maliyet, "icme_tasarruf_m3": icme_tasarruf_m3, "kwh": kwh, "isletme": isletme,
            "tasarruf_tl": tasarruf_tl, "yatirim": yatirim,
            "geri_odeme_yil": yatirim / tasarruf_tl if tasarruf_tl > 0 and yatirim > 0 else None, "eksik": eksik,
            "alt_sinir": bool(eksik)}
