"""
CoolScape AI – hava verisi iyileştirmeleri (Streamlit'ten bağımsız, test edilebilir):
  1. METAR düzeltmesi: Antalya Havalimanı (LTAI) resmî gözlemiyle (MGM üretir, aviationweather.gov yayımlar)
     bugünkü tahminin sapması ölçülür ve kalan saatlere azalarak uygulanır.
  2. Çok modelli tahmin: Open-Meteo üzerinden ECMWF IFS, DWD ICON ve NOAA GFS ayrı ayrı; modeller arası fark
     belirsizlik olarak gösterilir, istenirse en sıcak modelle (ihtiyatlı senaryo) plan yapılır.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

TR = ZoneInfo("Europe/Istanbul")
METAR_ISTASYON = "LTAI"
METAR_LAT, METAR_LON = 36.900, 30.798            # Antalya Havalimanı (METAR kaydındaki konum)
METAR_URL = "https://aviationweather.gov/api/data/metar"
METAR_EN_ESKI_DK = 90         # bundan eski gözlem düzeltmede kullanılmaz
DUZELTME_TAU_SA = 6.0         # sapmanın etkisi her 6 saatte e kat azalır (tahmin kendi seyrine döner)
DUZELTME_UST_C = 5.0          # bundan büyük sapma ölçüm/eşleştirme hatası sayılır, kullanılmaz
KNOT_MS = 0.514444

MODELLER = {"ecmwf_ifs025": "ECMWF IFS", "icon_seamless": "DWD ICON", "gfs_seamless": "NOAA GFS"}


# ---------------------------------------------------------------------------
# NEM: sıcaklık + çiy noktası ↔ bağıl nem (Magnus, Alduchov–Eskridge katsayıları)
# ---------------------------------------------------------------------------
def _es(t):
    return 6.1094 * np.exp(17.625 * np.asarray(t, dtype=float) / (np.asarray(t, dtype=float) + 243.04))


def bagil_nem(t, td):
    return np.clip(100.0 * _es(td) / _es(t), 0, 100)


def ciy_noktasi(t, rh):
    a = np.log(np.clip(np.asarray(rh, dtype=float), 1, 100) / 100.0) + 17.625 * np.asarray(t, dtype=float) / (
        243.04 + np.asarray(t, dtype=float))
    return 243.04 * a / (17.625 - a)


# ---------------------------------------------------------------------------
# 1. METAR
# ---------------------------------------------------------------------------
def metar_ayristir(kayitlar):
    """aviationweather.gov JSON listesi → zaman (İstanbul), sicaklik_c, ciy_c, bagil_nem, ruzgar_ms, ham."""
    s = []
    for k in kayitlar or []:
        if k.get("temp") is None or k.get("obsTime") is None:
            continue
        t, td = float(k["temp"]), (float(k["dewp"]) if k.get("dewp") is not None else np.nan)
        s.append({"zaman": datetime.fromtimestamp(int(k["obsTime"]), timezone.utc).astimezone(TR),
                  "sicaklik_c": t, "ciy_c": td, "bagil_nem": float(bagil_nem(t, td)) if np.isfinite(td) else np.nan,
                  "ruzgar_ms": float(k.get("wspd") or 0) * KNOT_MS, "ham": k.get("rawOb", "")})
    return pd.DataFrame(s).sort_values("zaman").reset_index(drop=True) if s else None


def _saatte(hava, alan, zaman):
    """Saatlik tablodan dakika hassasiyetinde doğrusal aradeğer (aynı gün)."""
    x = hava["saat"].values.astype(float)
    return float(np.interp(zaman.hour + zaman.minute / 60.0, x, hava[alan].values.astype(float)))


def metar_duzeltmesi(hava, metar, model_havalimani, simdi):
    """hava (kent noktası tahmini) için gözlem düzeltmesi. model_havalimani: aynı modelin havalimanı noktası tahmini.
    Dönüş: (düzeltilmiş hava, bilgi sözlüğü) ya da (hava, None) – gözlem yoksa, eskiyse ya da sapma şüpheliyse."""
    if metar is None or metar.empty or model_havalimani is None:
        return hava, None
    son = metar.iloc[-1]
    yas_dk = (simdi - son["zaman"]).total_seconds() / 60
    if yas_dk > METAR_EN_ESKI_DK or yas_dk < -15:
        return hava, {"durum": f"son gözlem {int(yas_dk)} dk önce; düzeltme yapılmadı"}
    t_m = _saatte(model_havalimani, "sicaklik_c", son["zaman"])
    rh_m = _saatte(model_havalimani, "bagil_nem", son["zaman"])
    sapma_t = float(son["sicaklik_c"]) - t_m
    sapma_td = (float(son["ciy_c"]) - float(ciy_noktasi(t_m, rh_m))) if np.isfinite(son["ciy_c"]) else 0.0
    if abs(sapma_t) > DUZELTME_UST_C or abs(sapma_td) > 2 * DUZELTME_UST_C:
        return hava, {"durum": f"sapma şüpheli ({sapma_t:+.1f} °C); düzeltme yapılmadı"}
    h = hava.copy()
    t0 = son["zaman"].hour + son["zaman"].minute / 60.0
    w = np.where(h["saat"].values >= t0 - 0.5, np.exp(-np.maximum(h["saat"].values - t0, 0) / DUZELTME_TAU_SA), 0.0)
    td = ciy_noktasi(h["sicaklik_c"].values, h["bagil_nem"].values) + w * sapma_td
    h["sicaklik_c"] = h["sicaklik_c"].values + w * sapma_t
    td = np.minimum(td, h["sicaklik_c"].values)
    h["bagil_nem"] = np.round(bagil_nem(h["sicaklik_c"].values, td), 1)
    h.attrs = dict(hava.attrs)
    return h, {"durum": "uygulandı", "zaman": son["zaman"], "olcum_t": float(son["sicaklik_c"]), "model_t": t_m,
               "sapma_t": sapma_t, "olcum_rh": float(son["bagil_nem"]), "sapma_td": sapma_td, "ham": son["ham"]}


# ---------------------------------------------------------------------------
# 2. ÇOK MODEL
# ---------------------------------------------------------------------------
def coklu_model_ayristir(hourly, tablo_fn):
    """Open-Meteo 'models=a,b,c' yanıtının hourly bölümü → {model: saatlik tablo}. Değişken adları '<ad>_<model>'.
    tablo_fn: tek modelli 'hourly' sözlüğünü uygulamanın tablosuna çeviren işlev. Eksik model atlanır."""
    sonuc = {}
    for kod in MODELLER:
        alt = {"time": hourly["time"]}
        for anahtar, deger in hourly.items():
            if anahtar.endswith("_" + kod):
                alt[anahtar[: -len(kod) - 1]] = deger
        if "temperature_2m" not in alt or all(v is None for v in alt["temperature_2m"]):
            continue
        try:
            df = tablo_fn(alt)
        except KeyError:          # modelde gerekli bir değişken yok: model atlanır
            continue
        bos = [c for c in df.columns if c not in ("zaman", "saat") and df[c].isna().all()]
        if {"sicaklik_c", "bagil_nem", "ruzgar_ms", "gunes_wm2"} & set(bos):
            continue
        df = df.drop(columns=bos)  # yalnız isteğe bağlı sütun (ör. doğrudan ışınım) boşsa düşülür
        for c in df.columns:
            if c not in ("zaman", "saat") and df[c].isna().any():
                df[c] = df[c].interpolate(limit_direction="both")   # 3 saatlik model çıktısı saatliğe
        sonuc[kod] = df
    return sonuc


def model_ozeti(modeller, saatler):
    """Her model için plan saatlerindeki en yüksek sıcaklık ve en düşük nem; sıcaktan soğuğa."""
    s = []
    for kod, df in modeller.items():
        g = df[df["saat"].isin(saatler)]
        if g.empty:
            continue
        s.append({"kod": kod, "model": MODELLER[kod], "en_yuksek_c": float(g["sicaklik_c"].max()),
                  "en_dusuk_nem": float(g["bagil_nem"].min())})
    return pd.DataFrame(s).sort_values("en_yuksek_c", ascending=False).reset_index(drop=True) if s else None
