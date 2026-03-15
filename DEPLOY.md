# Déploiement CryptoScan en ligne

## Fichiers nécessaires

```
cs6/
├── cryptoscan.html      ← l'application
├── server_prod.py       ← serveur de production
├── Dockerfile           ← pour Railway (Docker)
├── Procfile             ← pour Railway (Heroku mode)
├── railway.json         ← config Railway
└── requirements.txt     ← dépendances (vide, stdlib seulement)
```

---

## Option 1 — Railway (RECOMMANDÉ, gratuit, 5 minutes)

Railway est la solution la plus simple. Pas de carte bancaire pour commencer.

### Étapes

#### 1. Créer un compte Railway
Allez sur https://railway.app et connectez-vous avec votre compte GitHub.

#### 2. Mettre les fichiers sur GitHub
1. Créez un repo GitHub (ex: `cryptoscan`)
2. Copiez les 6 fichiers du dossier `cs6` dans ce repo
3. Faites un commit et push

#### 3. Déployer sur Railway
1. Sur Railway, cliquez **New Project → Deploy from GitHub repo**
2. Sélectionnez votre repo `cryptoscan`
3. Railway détecte le Dockerfile et lance le build automatiquement

#### 4. Ajouter la clé API Anthropic (optionnel)
Si vous voulez que la clé soit partagée pour tous les utilisateurs :
1. Dans votre projet Railway → **Variables**
2. Ajoutez : `ANTHROPIC_API_KEY` = `sk-ant-votre-clé-ici`

> Sans cette variable, chaque utilisateur devra entrer sa propre clé dans l'interface.

#### 5. Obtenir l'URL
Railway vous donne une URL du type :
`https://cryptoscan-production-xxxx.up.railway.app`

Vous pouvez y accéder depuis votre téléphone immédiatement !

---

## Option 2 — Render (gratuit, similaire à Railway)

1. Créez un compte sur https://render.com
2. New → **Web Service** → connectez votre repo GitHub
3. Paramètres :
   - **Runtime** : Docker (ou Python)
   - **Build Command** : *(laisser vide)*
   - **Start Command** : `python server_prod.py`
4. Ajoutez la variable d'environnement `ANTHROPIC_API_KEY` si souhaité
5. Cliquez **Create Web Service**

> Note : Sur Render en tier gratuit, le service "dort" après 15 min d'inactivité.

---

## Option 3 — VPS (DigitalOcean, OVH, Contabo, etc.)

Si vous avez un VPS, c'est la solution la plus stable.

```bash
# Sur votre VPS (Ubuntu/Debian)
apt update && apt install python3 git -y

# Cloner vos fichiers
git clone https://github.com/VOTRE_USER/cryptoscan.git /opt/cryptoscan
cd /opt/cryptoscan

# Lancer avec systemd (tourne en arrière-plan)
cat > /etc/systemd/system/cryptoscan.service << EOF
[Unit]
Description=CryptoScan
After=network.target

[Service]
WorkingDirectory=/opt/cryptoscan
ExecStart=/usr/bin/python3 server_prod.py
Restart=always
Environment=PORT=8080
Environment=ANTHROPIC_API_KEY=sk-ant-votre-clé-ici

[Install]
WantedBy=multi-user.target
EOF

systemctl enable cryptoscan
systemctl start cryptoscan
```

---

## Enregistrer un nom de domaine

### Registrars recommandés
- **Namecheap** : https://www.namecheap.com (souvent moins cher)
- **OVH** : https://www.ovh.com (interface française)
- **Porkbun** : https://porkbun.com (très bon prix)

### Domaines suggérés
- `cryptoscan.app` (~$14/an)
- `cryptoscan.io` (~$30/an)
- `cryptoscan.fr` (~$10/an)
- `deepscan.crypto` (domaine crypto natif)

### Pointer le domaine vers Railway

1. Dans Railway : **Settings → Domains → Add Custom Domain**
2. Entrez votre domaine (ex: `cryptoscan.app`)
3. Railway affiche un enregistrement CNAME à ajouter
4. Dans votre registrar, allez dans **DNS Management** et ajoutez :
   - Type : `CNAME`
   - Nom : `@` (ou `www`)
   - Valeur : ce que Railway vous donne (ex: `xxx.railway.app`)
5. Attendez 5-60 min pour la propagation DNS
6. Railway active le HTTPS automatiquement (certificat Let's Encrypt gratuit)

---

## Accès depuis téléphone

Une fois déployé avec votre domaine, votre app est accessible depuis n'importe quel appareil :
- Téléphone iOS/Android via Safari ou Chrome
- Tablette
- Depuis n'importe où dans le monde

L'interface est optimisée pour mobile avec :
- Navigation défilante horizontale
- Cartes en colonne unique
- Boutons adaptés aux écrans tactiles (min 44px)
- Champs de saisie agrandis

---

## Sécurité en production

- La clé API Anthropic n'est **jamais** stockée dans les fichiers du repo
- Utilisez **uniquement** la variable d'environnement `ANTHROPIC_API_KEY`
- Ne commitez jamais votre clé API dans Git
- Le serveur accepte aussi la clé depuis l'interface utilisateur (header `x-api-key`)

---

## Résumé rapide

| Étape | Action |
|-------|--------|
| 1 | Créer repo GitHub avec les 6 fichiers |
| 2 | S'inscrire sur Railway.app |
| 3 | Déployer depuis GitHub (auto-détection Docker) |
| 4 | Ajouter `ANTHROPIC_API_KEY` dans les variables Railway |
| 5 | Enregistrer un domaine (Namecheap, OVH...) |
| 6 | Ajouter le CNAME dans le DNS |
| 7 | Accéder depuis votre téléphone ✓ |
