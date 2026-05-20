# Flask Registratie App

Een Flask webapplicatie met een registratiepagina. Gebruikers kunnen een account aanmaken met voornaam, achternaam, e-mailadres en wachtwoord. Wachtwoorden worden versleuteld opgeslagen via bcrypt in een MySQL database.

## Projectstructuur

```
flask_registration/
├── app/
│   ├── __init__.py       # App factory, db- en bcrypt-initialisatie
│   ├── models.py         # User model
│   ├── forms.py          # WTForms registratieformulier
│   ├── routes.py         # URL-routes en view-functies
│   ├── templates/
│   │   ├── base.html
│   │   ├── register.html
│   │   └── success.html
│   └── static/
│       └── css/
│           └── style.css
├── config.py             # Configuratie via omgevingsvariabelen
├── run.py                # Startpunt van de app
├── requirements.txt
├── .env.example
└── .gitignore
```

## Vereisten

- Python 3.10+
- MySQL-server (lokaal draaien of via Docker)

## Lokaal opstarten

### 1. Repository klonen en map openen

```bash
git clone <repo-url>
cd flask_registration
```

### 2. Virtuele omgeving aanmaken en activeren

```bash
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows
```

### 3. Afhankelijkheden installeren

```bash
pip install -r requirements.txt
```

### 4. MySQL database aanmaken

Log in op uw MySQL-server en maak de database aan:

```sql
CREATE DATABASE flask_registration CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 5. Omgevingsvariabelen instellen

Kopieer het voorbeeldbestand en vul uw eigen waarden in:

```bash
cp .env.example .env
```

Open `.env` en pas de waarden aan:

```
SECRET_KEY=verander-dit-naar-een-veilige-sleutel
DB_HOST=localhost
DB_PORT=3306
DB_NAME=flask_registration
DB_USER=root
DB_PASSWORD=jouw_wachtwoord
```

### 6. App starten

```bash
python run.py
```

De app is nu bereikbaar op [http://localhost:5000](http://localhost:5000).

De databasetabellen worden automatisch aangemaakt bij de eerste start.

## Omgevingsvariabelen

| Variabele     | Omschrijving                        | Standaardwaarde       |
|---------------|-------------------------------------|-----------------------|
| `SECRET_KEY`  | Flask sessie/CSRF-sleutel           | `change-this-in-production` |
| `DB_HOST`     | MySQL hostnaam                      | `localhost`           |
| `DB_PORT`     | MySQL poortnummer                   | `3306`                |
| `DB_NAME`     | Naam van de database                | `flask_registration`  |
| `DB_USER`     | MySQL gebruikersnaam                | `root`                |
| `DB_PASSWORD` | MySQL wachtwoord                    | *(leeg)*              |
