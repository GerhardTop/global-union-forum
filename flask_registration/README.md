# Global Union Forum

Flask webapplicatie voor Global Union Forum — een politieke denktank die onderzoekt of het succes van de Europese Unie wereldwijd herhaald kan worden.

**Live:** https://global-union-forum-1.onrender.com
**Domein (in progress):** https://globalunionforum.org

## Tech stack

| Laag | Technologie |
|------|-------------|
| Backend | Python 3.11, Flask, Flask-SQLAlchemy, Flask-Login, Flask-Mail, Flask-Bcrypt, Flask-Limiter, Flask-Talisman |
| Database | PostgreSQL (productie via Render.com), MySQL (lokaal) |
| Auth | Formulier-login + Google OAuth via Authlib |
| Frontend | Jinja2 templates, GU Design System (eigen CSS) |
| AI | Claude API (Anthropic) voor forumvertalingen |
| Deployment | Render.com, GitHub Actions trigger via push |

## Features

- Registratie met e-mailverificatie
- Login met wachtwoord of Google OAuth
- LinkedIn-profiel verplicht voor forumdeelname
- Forum met threaded replies, likes, moderatie en sluiten/heropenen
- Automatische vertaling van forumberichten via Claude API (NL ↔ EN)
- Admin dashboard (gebruikersbeheer, rolbeheer)
- Moderatordashboard
- Meertalig: Nederlands en Engels
- Sessie-timeout na 30 minuten inactiviteit

## Lokaal opstarten

### 1. Repository klonen

```bash
git clone https://github.com/GerhardTop/global-union-forum.git
cd global-union-forum/flask_registration
```

### 2. Virtuele omgeving

```bash
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows
pip install -r requirements.txt
```

### 3. MySQL database aanmaken

```sql
CREATE DATABASE flask_registration CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 4. .env instellen

Maak een `.env` bestand in de `flask_registration/` map:

```
SECRET_KEY=verander-dit-naar-een-veilige-sleutel

# Lokale MySQL database
DB_HOST=localhost
DB_PORT=3306
DB_NAME=flask_registration
DB_USER=root
DB_PASSWORD=jouw_wachtwoord

# E-mail (Gmail voorbeeld)
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=jouw@gmail.com
MAIL_PASSWORD=jouw_app_wachtwoord

# Google OAuth
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...

# Claude API (optioneel — voor forumvertaling)
ANTHROPIC_API_KEY=...
```

### 5. Starten

```bash
python run.py
```

De app is bereikbaar op http://localhost:5000. Tabellen en demo-content worden automatisch aangemaakt bij de eerste start.

## Deployment (Render.com)

De app draait op Render.com en wordt automatisch gedeployed bij een push naar de `main` branch op GitHub (`GerhardTop/global-union-forum`).

Vereiste environment variabelen in Render dashboard:

| Variabele | Omschrijving |
|-----------|--------------|
| `SECRET_KEY` | Flask sessie/CSRF-sleutel |
| `DATABASE_URL` | Automatisch ingevuld door Render PostgreSQL add-on |
| `MAIL_SERVER` | SMTP server (bijv. `smtp.gmail.com`) |
| `MAIL_PORT` | SMTP poort (bijv. `587`) |
| `MAIL_USE_TLS` | `true` |
| `MAIL_USERNAME` | Afzenderadres |
| `MAIL_PASSWORD` | App-wachtwoord of SMTP token |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `ANTHROPIC_API_KEY` | Claude API key voor forumvertaling |

## Projectstructuur

```
flask_registration/
├── app/
│   ├── __init__.py        # App factory, extensies, migraties, seeding
│   ├── models.py          # User, Thread, Post, PostLike
│   ├── forms.py           # WTForms
│   ├── routes.py          # Alle routes en businesslogica
│   ├── translations.py    # NL/EN vertalingen
│   ├── utils.py           # Linkify filter
│   ├── templates/         # Jinja2 templates
│   └── static/            # CSS, afbeeldingen, uploads
├── config.py              # Configuratie via omgevingsvariabelen
├── run.py                 # Entrypoint
├── requirements.txt
├── Procfile               # gunicorn run:app
└── .gitignore
```
