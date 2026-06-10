# Analyse Vertaalstructuur Global Union Forum

**Datum:** 2026-06-10  
**Status:** Onderzoek alleen, geen wijzigingen

---

## 1. TRANSLATIONS.PY HUIDIGE INHOUD & OPZET

### Structuur
- **Type:** Dictionary-gebaseerde vertaalsamenstelling
- **Talen:** Nederlands (nl) en Engels (en)
- **Totaal vertaalsleutels:** 35 sleutels

### Inhoud
**Gegroepeerd per categorie:**

#### A. Navigatie & UI (4 sleutels)
- nav_profile / nav_logout (beide talen)

#### B. Welkomstpagina (6 sleutels)
- welcome_title, welcome_subtitle, welcome_login_btn, welcome_register_btn

#### C. Loginpagina (6 sleutels)
- login_title, login_email, login_password, login_btn, login_no_account, login_register_link

#### D. Registratiepagina (6 sleutels)
- register_title, register_subtitle, register_first_name, register_last_name, register_email, register_password, register_confirm_password, register_btn

#### E. Dashboard (3 sleutels)
- dashboard_title, dashboard_welcome, dashboard_placeholder_text

#### F. Profielpagina (7 sleutels)
- profile_title, profile_first_name, profile_last_name, profile_email, profile_change_password, profile_current_password, profile_new_password, profile_confirm_new_password, profile_save_btn

#### G. Succespagina (3 sleutels)
- success_greeting, success_subtitle, success_login_btn, success_register_btn

#### H. Flash-berichten (4 sleutels)
- flash_invalid_credentials, flash_email_in_use, flash_password_changed, flash_wrong_password

#### I. Placeholders formulieren (5 sleutels)
- ph_first_name, ph_last_name, ph_email, ph_password, ph_confirm_password

#### J. Validatiefouten (4 sleutels)
- val_required, val_email_invalid, val_password_min, val_passwords_no_match

### Huidige gebruik in codebase
- **Via context processor:** Alle 35 sleutels beschikbaar als `t.*` in templates
- **Via session direct:** Limitado gebruik in routes.py voor flash-berichten
- **Forum/Content:** Nul sleutels voor forum-gerelateerde teksten

---

## 2. INDEX.HTML: HARDCODED VS. DYNAMISCH

### Totaal tekstelemente: ~30 major text items

#### HARDCODED Nederlands (NIET VERTAALD): 15 items
| Regelnum | Tekst | Type | Opmerking |
|----------|-------|------|-----------|
| 10 | "Freedom for all" | Hardcoded Engels | eyebrow sectie |
| 89 | "Een wereldwijde beweging" | Hardcoded Nederlands | Flags sectie |
| 138 | "Het idee, in vier stukken" | Hardcoded Nederlands | eyebrow sectie |
| 139 | "Niet uit dwang, maar uit aantrekkingskracht" | Hardcoded Nederlands | h2 title |
| 140-143 | Lead text idee sectie | Hardcoded Nederlands | Volledige paragraaf |
| 153 | "Handelen op gedrag" | Hardcoded Nederlands | Principe 1 title |
| 154-158 | Body principe 1 | Hardcoded Nederlands | 5 regels |
| 166 | "Vreedzaam, maar niet naïef" | Hardcoded Nederlands | Principe 2 title |
| 167-171 | Body principe 2 | Hardcoded Nederlands | 5 regels |
| 179 | "Vrije informatie" | Hardcoded Nederlands | Principe 3 title |
| 180-184 | Body principe 3 | Hardcoded Nederlands | 5 regels |
| 192 | "Eén handelszone — geen open grenzen" | Hardcoded Nederlands | Principe 4 title |
| 193-197 | Body principe 4 | Hardcoded Nederlands | 5 regels |

#### DYNAMISCH VERTAALD (MET {% if lang %}) : 15 items
Alle teksten in hero lead sections (regels 15-63), feedback section (regels 212-244), en modals (regels 97-132).

### Samenvattingpercentage index.html
- **Hardcoded:** ~50% van de tekstlading
- **Dynamisch:** ~50%
- **Probleem:** Hele idea-sectie (4 principes + intro) is hardcoded Nederlands

---

## 3. ANALYSE ANDERE TEMPLATES

### Totaal templates: 25 HTML-bestanden (1.649 regels code)

#### A. Templates met GOED vertaalgebruik (19 files)
| Template | {% if lang %} count | Status |
|----------|-----------|--------|
| manifest.html | 42 | Excellente dekkding |
| about.html | 23 | Goede dekkding |
| aanmelden.html | 23 | Goede dekkking |
| forum/thread.html | 20 | Goede dekkking |
| forum/_post.html | 22 | Goede dekkking |
| profile.html | 21 | Goede dekkking |
| wachtwoord_reset.html | 13 | Goed |
| wachtwoord_vergeten.html | 9 | Goed |
| forum/new_thread.html | 11 | Goed |
| forum/index.html | 10 | Goed |
| forum/closed.html | 10 | Goed |
| forum/edit_thread.html | 10 | Goed |
| linkedin_aanvullen.html | 8 | Goed |
| login.html | 5 | Matig* |
| landing.html | 5 | Matig |
| _header.html | 2 | Minimaal |
| base.html | 2 | Minimaal |
| _footer.html | 2 | Minimaal |
| privacy.html | 2 | Matig |

*login.html heeft extra hardcoded Engelse strings in JavaScript (regels 112-137)

#### B. Templates met ZWAK vertaalgebruik (6 files)
| Template | {% if lang %} count | Probleem |
|----------|-----------|----------|
| admin/dashboard.html | 0 | Geen vertaallogica |
| moderator/dashboard.html | 0 | Geen vertaallogica |
| register.html | 2 | Register redirect - minimal |
| success.html | 0 | Geen vertaallogica |
| welcome.html | 0 | Geen vertaallogica |
| manifest.html | -- | Zie hierboven (goed) |

#### C. Hardcoded teksten per template (overzicht)
| Category | Templates | Aantal hardcoded items | Totaal regels |
|----------|-----------|----------------------|----------------|
| Footer | _footer.html | 3 Engels/Nederlands mix | 49 |
| Header | _header.html | 2 (Forum, Manifest) | 90 |
| Admin pages | admin/*.html | 15+ | 366 |
| Forum sections | forum/index.html | 1 (Forum title) | 78 |
| Privacy | privacy.html | Full content split | 100 |

### Schatting hardcoded teksten ALLE templates
- **manifest.html:** 0% hardcoded (volledig vertaald)
- **about.html:** 5% hardcoded (1-2 labels)
- **index.html:** 50% hardcoded (hele idea-sectie)
- **forum/*.html:** 5-10% hardcoded (labels, badge-teksten)
- **profile.html:** 5% hardcoded
- **login.html:** 15% hardcoded (JavaScript strings)
- **admin/*:** 80% hardcoded (geen vertaallogica)
- **moderator/*:** 80% hardcoded (geen vertaallogica)
- **privacy.html:** 50% hardcoded (volledige conditionele blokken)

**Globale schatting:** ~25-30% van alle teksten hardcoded in én taal

---

## 4. MIGRATIEOMVANG NAAR FLASK-BABEL

### Wat is Flask-Babel?
Flask-Babel is een extensie voor i18n (internationalization) dat:
- `gettext` families (PO/POT files) hanteert
- Automatische extractie van strings via `pybabel extract`
- Runtime translation via `pybabel compile`
- Jinja2 template functies: `_()`, `ngettext()`, `gettext()`

### Migratieomvang schatting

#### A. Code-wijzigingen (routes.py)
**Hudig:** Inline if-statements in templates  
**Na migratie:**
- Flash-berichten: `_('Wachtwoord gewijzigd')` in plaats van dict lookup
- Routes: `_('message')` wrapper rond alle user-facing strings
- **Omvang:** ~40-50 wijzigingen in routes.py

#### B. Template-wijzigingen
**Hudig:** `{% if lang == 'nl' %}...{% else %}...{% endif %}`  
**Na migratie:** `{{ _('...') }}`

**Omvang per template:**

| Template | Hudig conditionals | Na migratie markings | Inspanning |
|----------|------------------|----------------------|-----------|
| index.html | 20 | 20+ markings | HOOG |
| manifest.html | 42 | 42+ markings | HOOG |
| about.html | 23 | 23+ markings | MEDIUM |
| forum/* (6 files) | 83 | 83+ markings | HOOG |
| login.html | 5 + JS | 8+ markings | MEDIUM |
| profile.html | 21 | 21+ markings | MEDIUM |
| admin/* (2 files) | 0 | +50 markings | HOOG |
| moderator/* (1 file) | 0 | +30 markings | MEDIUM |
| privacy.html | 2 | +100 markings | HOOG |
| Overige (13 files) | 40 | 40+ markings | MEDIUM |

**Totaal template markings:** ~330-380 `{{ _() }}` calls

#### C. Configuratiebestanden
**Nieuw toe te voegen:**
```
babel.cfg
translations/nl/LC_MESSAGES/messages.po
translations/nl/LC_MESSAGES/messages.mo
translations/en/LC_MESSAGES/messages.po
translations/en/LC_MESSAGES/messages.mo
```
**Inspanning:** LOW (1-2 uur setup)

#### D. Build & Deployment Pipeline
**Nieuw:**
- `pybabel extract -F babel.cfg -o messages.pot .`
- `pybabel init -i messages.pot -d translations -l nl`
- `pybabel init -i messages.pot -d translations -l en`
- `pybabel compile -d translations`

**In CI/CD:** Babel compile stap voorafgaand aan deployment

#### E. Plural handling
**Huidig:** Geen pluralisatie (hardcoded "reactie/reacties" in templates)  
**Na migratie:** `ngettext()` nodig voor:
- Forum reactie counts
- User counts  
- Post counts
- Datums ("1 day ago" vs "3 days ago")

**Inspanning:** MEDIUM (20-30 ngettext calls)

### Totale migratieomvang schatting

| Taak | Bestanden | Wijzigingen | Uren |
|------|-----------|-----------|-------|
| routes.py aanpassen | 1 | ~45 _() wraps | 2-3 |
| Template conversie | 25 | ~330-380 _() markings | 8-12 |
| translations.py verwijderen | 1 | Verwijderen + config | 0.5 |
| babel.cfg + init | -- | Setup + compile stappen | 1-2 |
| Plural handling | 5+ templates | ~25 ngettext() calls | 2-3 |
| Testing & QA | -- | Verificatie beide talen | 3-4 |
| **TOTAAL** | -- | -- | **17-25 uren** |

### Risico's & beperkingen

#### Laag risico
- ✅ Geen database schema wijzigingen
- ✅ Kan incrementeel gerolled out worden
- ✅ Flask-Babel is stabiel & volwassen

#### Medium risico
- ⚠️ JavaScript strings in templates (login.html, aanmelden.html)
  - Oplossing: Data-attributes of API endpoints gebruiken
- ⚠️ Forum content met user-generated translations
  - Huident: Custom body_nl/body_en velden in database
  - Na migratie: Deze blijven buiten gettext (correct)

#### Hoog risico
- ❌ Geen: Veiligheid en stabiliteit niet bedreigd

### Voordelen van Flask-Babel migratie
1. **Standaardisatie:** Industry-standard i18n approach
2. **Schaalbaarheid:** Gemakkelijk meer talen toevoegen (FR, DE, etc.)
3. **Onderhoud:** Vertaalbestanden gescheiden van code
4. **Tooling:** Integratatie met Crowdin/Lokalise mogelijk
5. **Plurals:** Native support voor complexe taalkwesties
6. **Caching:** `.mo` files kunnen gecached worden

### Nee-redenen tegen migratie (huidge staat behouden)
1. **Eenvoudig:** Huidigen translations.py werkt goed
2. **Klein volume:** Slechts 2 talen (FR/DE later toevoegen is lastig)
3. **Snel:** Geen afhankelijkheden op externe build tools
4. **Beheersbaar:** 35 sleutels makkelijk te overzien

---

## SAMENVATTING

### Huide vertaalstructuur
- **Sterk:** Centraal gestuurde translations.py, context processor injectie
- **Zwak:** 
  - Veel hardcoded teksten in templates (~25-30%)
  - Geen strategie voor forum-user-content
  - Admin & moderator templates onvertaald
  - JavaScript strings niet gehandeld

### index.html specifieke bevindingen
- **Bug impact:** Hele idea-sectie (4 principes + introtekst) is hardcoded Nederlands
- **User-facing:** Eerste page impression is ~50% Engels, ~50% Nederlands
- **Scope:** ~13-15 tekstblokken moeten naar translations.py of verwijderen naar conditionals

### Migratieadvies
- **Klein project:** Huidence translations.py + fixes is kosteneffectief
- **Gemiddeld project:** Flask-Babel vraagt 17-25 uren, maar biedt toekomsttoegang
- **Prioriteit:** Focus eerst op index.html & admin/moderator templates (55% van problem)

---

## AANBEVELINGEN (niet geïmplementeerd)

### Quick-fix (2-3 uren)
1. Move alle index.html tekstblokken naar translations.py
2. Update admin/moderator templates met `{% if lang %}` blocks
3. Move JavaScript strings naar data-attributes

### Midterm (17-25 uren)
1. Volledige migratie naar Flask-Babel
2. Toevoegen plural handling
3. Setup CI/CD voor babel compile

### Longterm
1. Platform (Crowdin/Lokalise) integratie voor crowdsourced vertalingen
2. Additionele talen (FR, DE, ES) automatiseren
