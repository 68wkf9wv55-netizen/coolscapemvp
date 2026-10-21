"""
CoolScape AI – ağaç örtüsü (gölge) katmanı. Kaynak: ESA WorldCover 2021 v200, 10 m (CC BY 4.0).

Pilot bölge penceresini okur ve H3 altıgenlerine (çözünürlük 9) her sınıfın alan payını toplar.
Uygulama "agac_orani"nı gölge vekili olarak kullanır: altıgendeki yayaların bu oranı ağaç gölgesinde kabul edilir
(SolarCal f_bes = 1 − ağaç oranı). Ağaç (10) çimden (30) ayrıldığı için NDVI'dan daha iyi bir gölge vekilidir.

Çıktı: golge_h3.csv  (hex, agac_orani, cim_orani, yapi_orani, su_orani, lat, lon)
       park_yesil.csv (osm_id, park, yesil_pay, agac_pay, cim_pay, piksel): parklar_osm.json varsa her park poligonunun
       içindeki ağaç + çim payı; uygulama parkın sulanan alanını bununla hesaplar.
Kurulum: pip install rasterio h3 pandas numpy
Çalıştırma: python golge_hazirla.py
            python golge_hazirla.py --dosya ESA_WorldCover_10m_2021_v200_N36E030_Map.tif   (elle indirdiyseniz)

Elle indirme: https://worldcover2021.esa.int/downloader adresinde N36E030 karosunu seçin
(ya da AWS S3: esa-worldcover kovası, v200/2021/map/ klasörü).
"""

import argparse
import os
import sys

import h3
import numpy as np
import pandas as pd

KARO = "ESA_WorldCover_10m_2021_v200_N36E030_Map.tif"
ADRESLER = [
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/" + KARO,
    "https://esa-worldcover.s3.amazonaws.com/v200/2021/map/" + KARO,
]
BBOX = (30.62, 36.85, 30.80, 36.97)  # batı, güney, doğu, kuzey (uygulamadakiyle aynı)
SINIF = {"agac_orani": (10,), "cim_orani": (30,), "yapi_orani": (50,), "su_orani": (80,)}


def pencere_oku(yol):
    import rasterio
    from rasterio.windows import from_bounds
    with rasterio.Env(GDAL_HTTP_MAX_RETRY="3", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        with rasterio.open(yol) as r:
            w = from_bounds(*BBOX, transform=r.transform).round_offsets().round_lengths()
            a = r.read(1, window=w)
            t = r.window_transform(w)
    satir, sutun = np.indices(a.shape)
    lon = t.c + (sutun + 0.5) * t.a
    lat = t.f + (satir + 0.5) * t.e
    pencere_oku.son = (a, t)          # park poligonları için ham pencere
    return a.ravel(), lat.ravel(), lon.ravel()


def park_halkalari(yol):
    """parklar_osm.json (overpass out geom) → [(osm_id, ad, [halka koordinatları (lon, lat)], ...)]."""
    import json
    el = json.load(open(yol, encoding="utf-8")).get("elements", [])
    cikti = []
    for e in el:
        t = e.get("tags", {})
        if e.get("type") == "way" and e.get("geometry"):
            halkalar = [e["geometry"]]
        elif e.get("type") == "relation":
            halkalar = [m["geometry"] for m in e.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
        else:
            continue
        halkalar = [[(c["lon"], c["lat"]) for c in h] for h in halkalar if len(h) >= 4]
        if halkalar:
            cikti.append((f"{e['type'][0]}{e['id']}", t.get("name", ""), halkalar))
    return cikti


def park_yesil_paylari(a, t, parklar):
    from rasterio.features import geometry_mask
    satirlar = []
    for osm_id, ad, halkalar in parklar:
        geom = [{"type": "Polygon", "coordinates": [h]} for h in halkalar]
        try:
            ic = ~geometry_mask(geom, out_shape=a.shape, transform=t, all_touched=False)
        except Exception:
            continue
        s = a[ic]
        s = s[s > 0]
        if len(s) == 0:
            continue
        agac, cim = float(np.mean(s == 10)), float(np.mean(s == 30))
        satirlar.append({"osm_id": osm_id, "park": ad, "yesil_pay": round(agac + cim, 3), "agac_pay": round(agac, 3),
                         "cim_pay": round(cim, 3), "piksel": int(len(s))})
    return pd.DataFrame(satirlar)


def h3_oranlari(sinif, lat, lon, cozunurluk=9):
    gecerli = sinif > 0                      # 0 = veri yok
    hexler = np.array([h3.latlng_to_cell(a, b, cozunurluk) for a, b in zip(lat[gecerli], lon[gecerli])])
    s = sinif[gecerli]
    df = pd.DataFrame({"hex": hexler})
    for ad, kodlar in SINIF.items():
        df[ad] = np.isin(s, kodlar).astype(float)
    o = df.groupby("hex").mean().round(3).reset_index()
    o["piksel"] = df.groupby("hex").size().values
    ll = [h3.cell_to_latlng(h) for h in o["hex"]]
    o["lat"] = [x for x, _ in ll]
    o["lon"] = [y for _, y in ll]
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dosya", help="Elle indirilen WorldCover karosu (.tif)")
    ap.add_argument("--h3", type=int, default=9)
    ap.add_argument("--cikti", default="golge_h3.csv")
    ap.add_argument("--parklar", default="parklar_osm.json", help="OSM park poligonları (varsa park_yesil.csv yazılır)")
    a = ap.parse_args()
    kaynaklar = [a.dosya] if a.dosya else ["/vsicurl/" + u for u in ADRESLER]
    for k in kaynaklar:
        try:
            print("okunuyor:", k)
            sinif, lat, lon = pencere_oku(k)
            break
        except Exception as hata:
            print("  olmadı:", hata)
    else:
        sys.exit("WorldCover karosu okunamadı. Karoyu elle indirip --dosya ile verin (bkz. dosyanın başındaki açıklama).")
    df = h3_oranlari(sinif, lat, lon, a.h3)
    df.to_csv(a.cikti, index=False)
    print(f"Yazıldı: {a.cikti} ({len(df)} altıgen). Ortalama ağaç örtüsü %{100 * df['agac_orani'].mean():.1f}, "
          f"yapı %{100 * df['yapi_orani'].mean():.1f}, çim %{100 * df['cim_orani'].mean():.1f}")
    if os.path.exists(a.parklar):
        ham, t = pencere_oku.son
        py = park_yesil_paylari(ham, t, park_halkalari(a.parklar))
        py.to_csv("park_yesil.csv", index=False)
        print(f"Yazıldı: park_yesil.csv ({len(py)} park). Ortalama park içi ağaç+çim payı %{100 * py['yesil_pay'].mean():.0f}")


if __name__ == "__main__":
    main()
