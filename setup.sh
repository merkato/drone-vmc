#!/bin/bash
# --- DRONE CORE VMS INSTALLER ---

read -p "Podaj domenę (np. drony.twoja.pl): " MY_DOMAIN
read -p "Podaj tajny klucz sesji (losowe znaki): " MY_SECRET

# Tworzenie struktury
touch streaming_v3.db

# Tworzenie pliku .env
cat <<EOF > .env
DOMAIN=$MY_DOMAIN
STORAGE_SECRET=$MY_SECRET
EOF

# Podstawowe logo (placeholder)
curl -o static/logo.png https://via.placeholder.com/150/000000/FFFFFF?text=DRONE+CORE

echo "✅ Konfiguracja gotowa."
echo "Uruchom: docker-compose up -d --build"