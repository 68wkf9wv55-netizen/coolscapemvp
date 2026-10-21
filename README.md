# CoolScape AI

Kentsel serinlik ve su tahsis motoru · Antalya Muratpaşa–Kepez pilotu · Siemens Türkiye Climathon / COP31

Canlı uygulama: https://cop31so-bjfwfgycfoqz7ltvuqiz5f.streamlit.app/

Sıcak hava dalgası günlerinde belediyenin sınırlı soğutma suyunu park sulama, sisleme ve yol ıslatma arasında
08:00–20:00 saat saat dağıtan karar destek sistemi. Gerçek hava tahmini, uydu ısı haritası, ağaç örtüsü ve nüfus
verisiyle her noktanın ısı stresini (UTCI) hesaplar; suyu MILP optimizasyonuyla dağıtır.

## Sekmeler

- **Günün planı:** metrikler, uydu üstünde ısı haritası, saatlik kart, yazdırılabilir operatör planı.
- **Su kaynağı ve yatırım:** parkların sezonluk sulama ihtiyacı ve bugünkü maliyeti; arıtılmış suya geçişte boru hattı, yatırım, tasarruf, geri ödeme; Sınıf A mevzuat koşulları; finansman seçenekleri. Kaynağı olmayan birim fiyatlar 0'dır ve sonuç alt sınır olarak gösterilir.
- **Saha kaydı:** uygulanan su ve önce/sonra ölçümleri.
- **Yöntem ve veri:** kaynaklar, varsayımlar, bütçe–serinlik eğrisi.

## Günlük kullanım (operatör)

1. Uygulama açılışta **bugünün** (akşamsa yarının) planını gösterir. Sezon dışında, ısı stresi beklenmiyorsa kayıtlı bir sıcak gün açılır.
2. Kenar çubuğundan günlük su bütçesini ve önceliği seçin.
3. **Günlük planı yazdırılabilir indir (HTML)** → tarayıcıda “Yazdır”. Sahada “Yapıldı” ve “Uygulanan m³” doldurulur.
4. Uygulanan satırları onay tablosunda işaretleyip **kaydedin**; varsa önce/sonra ölçümünü “Tek uygulama ya da saha ölçümü kaydet” formuna girin.

## Belediyenin kendi noktaları

Kenar çubuğu → Müdahale noktaları → şablon CSV'yi indirin, doldurun, yükleyin. Kalıcı olsun isterseniz dosyayı
`noktalar.csv` adıyla bu depoya koyun.

| Sütun | Açıklama |
|---|---|
| `ad` | Nokta adı (zorunlu) |
| `tur` | `park`, `sisleme` ya da `islatma` (zorunlu) |
| `lat`, `lon` | Konum, ondalık derece (zorunlu) |
| `sulanan_alan_m2` | Sulanan / ıslatılan alan |
| `kapasite_m3_saat` | Hat ya da araç kapasitesi |
| `su_kaynagi` | içme suyu, arıtılmış su, kuyu… |
| `vana` | Vana ya da araç kodu (plana yazılır) |
| `ilce` | İlçe |

Boş bırakılan alan ve kapasite türün temsili değeriyle doldurulur ve ekranda “temsili” diye etiketlenir.

## Dosyalar

| Dosya | İçerik |
|---|---|
| `coolscape_app.py` | Streamlit uygulaması (Main file path) |
| `coolscape_mvp.py` | Nokta yönetimi, gölge, operatör planı, kayıt modülleri |
| `coolscape_yatirim.py` | Su kaynağı ve yatırım hesabı: sulama ihtiyacı (FAO-56), boru hattı, tasarruf, mevzuat, finansman |
| `aat_osm.json` | (eklenecek) Arıtma tesisi konumları, overpass-turbo `man_made=wastewater_plant` |
| `requirements.txt` | Bağımlılıklar |
| `isi_yuzeyi_h3.csv` | Landsat 9 yüzey sıcaklığı, 25 Temmuz 2025 (+ Sentinel-2 NDVI), H3 çözünürlük 9 |
| `golge_h3.csv` | ESA WorldCover 2021 ağaç / yapı / çim oranı (`golge_hazirla.py`) |
| `parklar_osm.json` | OpenStreetMap parkları (overpass-turbo, `out geom`) |
| `tesisler_osm.json` | OpenStreetMap okul, sağlık, huzurevi, çeşme (ODbL) |
| `nufus_h3.csv` | WorldPop 2025 toplam nüfus ve 65+ (65+ ilçe oranıyla; CC BY 4.0) (`nufus_hazirla.py`) |
| `haritalar/` | QGIS 3.34 baskı haritaları (PNG, PDF) |
| `uyanik_tut.py`, `.github/workflows/uyanik_tut.yml` | Uygulamayı 3 saatte bir ziyaret ederek uyanık tutar |

## Kalıcı kayıt (Google Sheets, isteğe bağlı)

Yapılandırılmazsa kayıtlar geçici CSV'de tutulur ve uygulama yeniden başlayınca silinir (ekrandan indirip geri yüklenebilir).

1. Google Cloud'da hizmet hesabı oluşturun, JSON anahtarını indirin; Google Sheets API ve Google Drive API'yi açın.
2. Boş bir Google E-Tablosu oluşturup hizmet hesabının e-postasıyla **Düzenleyen** olarak paylaşın.
3. Streamlit Cloud → Settings → Secrets:

```toml
kayit_sheet_id = "E-TABLO-KİMLİĞİ"

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "..."
client_email = "..."
client_id = "..."
token_uri = "https://oauth2.googleapis.com/token"
```

## Veri durumu

- **Gerçek:** hava (Open-Meteo: tahmin ve ERA5 arşivi), yüzey sıcaklığı (Landsat), ağaç örtüsü (ESA WorldCover), park konum ve alanları (OSM), tesisler (OSM), nüfus (WorldPop).
- **Vekil:** kırılganlık göstergesi (okul / sağlık / huzurevi yakınlığı); gölge = ağaç örtüsü oranı (bina gölgesi yok).
- **Temsili** (belediye CSV'si ile değişir): hat kapasitesi, sulanan alan, pompa katsayısı; kişi/saat erişim katsayıları.
- Soğutma etki eğrileri literatür saha ölçümlerine göre ölçeklenmiştir; Antalya doğrulaması uygulamadaki saha ölçümü kayıtlarıyla (2027 pilotu) yapılacaktır.

## Yeniden üretim

```bash
python nufus_hazirla.py --klasor <WorldPop klasörü>      # nufus_h3.csv
python golge_hazirla.py                                   # golge_h3.csv
python cbs_hazirla.py --yuzey isi_yuzeyi_h3.csv --tesis tesisler_osm.json --nufus nufus_h3.csv
python cbs_poligon.py
QT_QPA_PLATFORM=offscreen python3 qgis_harita.py           # haritalar/ (PDF)
```
