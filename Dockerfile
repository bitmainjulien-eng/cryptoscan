FROM python:3.11-slim

# Répertoire de travail
WORKDIR /app

# Copier les fichiers de l'app
COPY cryptoscan.html .
COPY server_prod.py .
COPY requirements.txt .

# Port exposé (Railway/Render injecte la variable PORT automatiquement)
EXPOSE 8080

# Lancer le serveur
CMD ["python", "server_prod.py"]
