#!/bin/bash
# --- DRONE VMS INSTALLER ---

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

# Zatrzymanie w razie błędu
set -e

echo "--- 1. Usuwanie ewentualnych starych wersji ---"
sudo apt-get remove -y docker docker-engine docker.io containerd runc || true

echo "--- 2. Instalacja zależności i repozytorium Docker ---"
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update

echo "--- 3. Instalacja Docker Engine i Docker Compose ---"
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "--- 4. Konfiguracja sieci (eliminacja 172.x - rozwiązanie konfliktu VPN) ---"
sudo mkdir -p /etc/docker

# Tworzymy daemon.json
# bip: Adres dla domyślnego mostka docker0 (Twoja brama)
# default-address-pools: Pula, z której Docker Compose będzie tworzył sieci projektowe
sudo tee /etc/docker/daemon.json <<EOF
{
  "bip": "192.168.149.1/24",
  "default-address-pools": [
    {
      "base": "192.168.150.0/21",
      "size": 24
    }
  ]
}
EOF

echo "--- 5. Restart i czyszczenie sieci ---"
# Przeładowanie konfiguracji
sudo systemctl restart docker

# Jeśli wcześniej były jakieś sieci, czyścimy je, aby Docker nie trzymał starych tras
sudo docker network prune -f

echo "--- 6. Weryfikacja zmian ---"
# Sprawdzenie adresu docker0
DOCKER_IP=$(ip addr show docker0 | grep "inet " | awk '{print $2}')
echo "Główny mostek Docker (docker0) działa na: $DOCKER_IP"

# Sprawdzenie konfiguracji poola
echo "Konfiguracja pooli adresowych została załadowana."

echo "--- GOTOWE ---"
echo "Teraz Twój serwer jest bezpieczny dla VPN Cisco. Wszystkie kontenery"
echo "będą otrzymywać adresy z zakresu 192.168.149.x i 192.168.150.x."

echo "--- 7. Zarządzanie uprawnieniami użytkownika ---"
# Sprawdzamy, kto wywołał skrypt (jeśli przez sudo, używamy SUDO_USER)
REAL_USER=${SUDO_USER:-$USER}

if [ "$REAL_USER" = "root" ]; then
    echo "BŁĄD: Skrypt powinien być uruchomiony przez sudo z konta zwykłego użytkownika,"
    echo "a nie bezpośrednio z konta root, aby poprawnie przypisać uprawnienia."
else
    echo "Dodawanie użytkownika $REAL_USER do grupy docker..."
    sudo usermod -aG docker $REAL_USER
    
    echo "--- KONFIGURACJA ZAKOŃCZONA ---"
    echo "WAŻNE: Aby zmiany w grupach weszły w życie bez restartu serwera,"
    echo "wykonaj teraz komendę:"
    echo "newgrp docker"
    echo ""
    echo "✅ Konfiguracja gotowa."
    echo "Uruchom: docker-compose up -d --build"
fi