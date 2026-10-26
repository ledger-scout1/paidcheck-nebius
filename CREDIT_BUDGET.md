# Buget de credite — Nebius edition (doar credite gratuite, zero cheltuieli)

Surse de credite: Nebius Builder Program (gratuit) → **$25 Token Factory + $25 Tavily**.
Regula fermă a proiectului: niciun card, nicio reîncărcare automată. Dacă creditul se termină,
demo-ul trece în mod offline-fixture (funcțional), nu cumpără nimic.

## Cost real per verificare completă (live)
| Componentă | Apeluri | Consum estimat | Cost estimat |
|---|---|---|---|
| Nemotron — înțelegere claim (doar dacă parserul lasă câmpuri goale) | 0–1 | ~500 tok in / ~120 out | ≤ $0.0003 |
| Nemotron — explicație verdict | 1 | ~700 tok in / ~300 out | ≤ $0.0005 |
| Tavily — context public (basic, 1 credit) | 0–1 | 1 credit | $0.008 (tarif pay-as-you-go) |
| **Total per verificare** | | | **≤ $0.001 (Nemotron) + ≤ $0.008 (Tavily)** |

Prețuri de referință (catalog Token Factory, de reconfirmat la smoke test): modelele Nemotron
Nano/Super sunt în zona $0.05–0.40 / 1M tokeni input și $0.20–0.60 / 1M output — ordinul de
mărime de mai sus rămâne valabil chiar și la capătul scump al intervalului.

## Ajung pentru jurizare (1–15 dec 2026)?
- Token Factory $25 la ≤$0.001/verificare → **≥25.000 verificări**. Necesar realist pentru
  jurizare + trafic de demo: sute. Marjă: **>25×**.
- Tavily $25 ≈ 3.125 credite (la $0.008/credit) → **~3.100 căutări basic**. Marjă: **>10×**.
- Buget intern de dezvoltare/testare până la depunere: max **$3** din fiecare credit;
  restul rămâne neatins pentru perioada de jurizare.

## Protecții în cod (nu doar pe hârtie)
- `web_demo.py`: plafon zilnic de apeluri live (`DEMO_DAILY_LIVE_CAP`, implicit 300/zi);
  peste plafon, demo-ul continuă offline și o spune pe pagină.
- Tavily: doar `search_depth=basic` (1 credit), `max_results=3`, fără extract/crawl.
- Nemotron: `max_tokens` mic (300 înțelegere / 500 explicație), temperatură 0.1.
- Fără niciun mecanism de top-up în cod; cheile citesc doar creditele Builder existente.

## Găzduirea demo-ului până la 15 dec (cost $0)
- Opțiunea 1: **Hugging Face Spaces (Docker, CPU free)** — URL public persistent, fără card;
  spațiul adoarme la inactivitate și se trezește la vizită (~30–60s; notăm asta în textul
  Devpost ca juriul să aștepte trezirea, nu să creadă că demo-ul e mort).
- Opțiunea 2: Render free tier (același comportament sleep/wake).
- Fallback permanent: repo-ul rulează local în <5 minute (`python3 web_demo.py`), instrucțiuni
  în README — demo-ul nu depinde de un singur furnizor gratuit.
- Aplicația e stdlib-only (fără dependențe plătite/de instalat greu) tocmai ca găzduirea
  gratuită să nu devină un punct de eșec.
