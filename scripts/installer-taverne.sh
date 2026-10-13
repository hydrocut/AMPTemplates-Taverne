#!/bin/bash
# installer-taverne.sh - pose par le template AMP TeamKit (recree a chaque Update).
#
#   installer-taverne.sh "source=github:hydrocut/taverne" "jeton=<jeton de lecture>" "version="
#
# Dossier de l'instance (taverne/) :
#   node/          Node.js 22, binaire officiel de nodejs.org (version et empreinte figees ici)
#   jeu/           la version installee de La Taverne (serveur, moteur, client, VERSION)
#   jeu.ancien/    la version d'avant (retour rapide possible)
#   donnees/       stats, profils, tables sauvees, moderation : JAMAIS touche par une mise a jour
#   sauvegardes/   copie de donnees/ faite avant chaque changement de version (5 gardees)
#
# Le code du jeu est prive : la version est telechargee avec un jeton de LECTURE, puis son
# empreinte SHA-256 est verifiee (fichier .sha256 publie a cote, et celle calculee par GitHub
# quand elle existe), puis chaque fichier est verifie (EMPREINTES.sha256 de l'archive).
# Le jeton n'est jamais affiche ni passe sur la ligne de commande de curl (fichier d'en-tete
# lisible par l'instance seule, efface en sortant).
set -o pipefail
cd "$(dirname "$0")" || exit 1
ICI="$PWD"
umask 027
export LANG=C.UTF-8 LC_ALL=C.UTF-8

NODE_V=v22.23.3
# SHA-256 de node-v22.23.3-linux-x64.tar.xz, relevee dans https://nodejs.org/dist/v22.23.3/SHASUMS256.txt le 13/10/2026
NODE_SHA=df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de
NODE_URL="${TAVERNE_NODE_URL:-https://nodejs.org/dist}"

declare -A V
for a in "$@"; do
  case "$a" in
    *=*) V["${a%%=*}"]="${a#*=}" ;;
    *) echo "ERREUR : argument inattendu."; exit 1 ;;
  esac
done
SOURCE="${V[source]// /}"
JETON="${V[jeton]}"
VOULUE="${V[version]// /}"
VOULUE="${VOULUE#v}"

echec() { echo "ERREUR : $1"; exit 1; }
etape() { echo "TeamKit : $1"; }
# Chemins relatifs au dossier de l'instance (le script y est deja).
AUTH=".cache/auth-$$"
REP=".cache/rep-$$"
SHA_F=""
ARCHIVE=""
nettoyer() { rm -f "$AUTH" "$REP" ${SHA_F:+"$SHA_F"} ${ARCHIVE:+"$ARCHIVE"}; }
trap nettoyer EXIT

for outil in curl tar sha256sum; do
  command -v "$outil" >/dev/null 2>&1 || echec "l'outil $outil manque dans l'image du conteneur."
done
mkdir -p .cache donnees sauvegardes || echec "impossible de creer les dossiers de l'instance."

# --- 1. Node.js 22 --------------------------------------------------------------------
if [ "$(node/bin/node --version 2>/dev/null)" != "$NODE_V" ]; then
  command -v xz >/dev/null 2>&1 || echec "l'outil xz manque dans l'image du conteneur."
  etape "Node.js $NODE_V (binaire officiel)..."
  F="node-$NODE_V-linux-x64.tar.xz"
  curl -fsSL --retry 3 --connect-timeout 20 -o ".cache/$F" "$NODE_URL/$NODE_V/$F" || echec "telechargement de $F impossible ($NODE_URL)."
  if [ "$(sha256sum ".cache/$F" | cut -d' ' -f1)" != "$NODE_SHA" ]; then
    rm -f ".cache/$F"
    echec "empreinte SHA-256 de $F incorrecte : fichier abime ou modifie, rien n'est installe."
  fi
  rm -rf node.new && mkdir -p node.new
  tar -xJf ".cache/$F" -C node.new --strip-components=1 --no-same-owner || { rm -rf node.new; echec "archive $F illisible."; }
  rm -f ".cache/$F"
  [ "$(node.new/bin/node --version 2>/dev/null)" = "$NODE_V" ] || { rm -rf node.new; echec "Node.js $NODE_V ne demarre pas dans ce conteneur."; }
  rm -rf node.old; [ -d node ] && mv node node.old
  mv node.new node && rm -rf node.old
fi
echo "Node.js $(node/bin/node --version)"

# --- 2. Ou chercher la version -----------------------------------------------------------
VERSION_RE='^[0-9]+\.[0-9]+\.[0-9]+([-+][A-Za-z0-9.]+)?$'
[ -z "$VOULUE" ] || [[ "$VOULUE" =~ $VERSION_RE ]] || echec "version voulue '$VOULUE' : vide (la derniere) ou un numero comme 0.1.0."
[ -n "$SOURCE" ] || echec "'Adresse des versions' est vide : mets github:hydrocut/taverne (voir le README)."
[[ "$JETON" =~ ^[A-Za-z0-9_.=+/-]*$ ]] || echec "le jeton de lecture contient un espace ou un caractere inattendu (mal copie ?)."

: > "$AUTH" && chmod 600 "$AUTH"
[ -n "$JETON" ] && printf 'Authorization: Bearer %s\n' "$JETON" > "$AUTH"
# curl avec le jeton (en-tete lu dans un fichier). Le jeton n'est envoye qu'a l'hote demande :
# curl le retire quand GitHub redirige vers son stockage.
lire() { curl -sS -L --retry 3 --connect-timeout 20 -H @"$AUTH" "$@"; }

http_code() { # url fichier [en-tetes...] -> code HTTP ; corps dans fichier
  local url="$1" sortie="$2" c; shift 2
  c=$(lire -o "$sortie" -w '%{http_code}' "$@" "$url" 2>/dev/null)
  [[ "$c" =~ ^[0-9]{3}$ ]] && echo "$c" || echo 000
}
expliquer() { # code quoi
  case "$1" in
    401) echec "$2 : jeton refuse (expire, revoque ou mal copie). Cree un nouveau jeton de lecture et colle-le dans le champ 'Jeton de lecture des versions'." ;;
    403) echec "$2 : acces refuse (le jeton n'a pas le droit de lire ce depot, ou trop de demandes : reessaie dans une heure)." ;;
    404) echec "$2 : introuvable (adresse, numero de version, ou le jeton ne voit pas ce depot prive)." ;;
    000) echec "$2 : le serveur ne repond pas (reseau du conteneur ?)." ;;
    *) echec "$2 : reponse HTTP $1 inattendue." ;;
  esac
}

CIBLE=""; SHA_PUBLIEE=""; DIGEST=""
if [[ "$SOURCE" =~ ^github:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$ ]]; then
  DEPOT="${BASH_REMATCH[1]}"
  # TAVERNE_GITHUB_API : seulement pour les essais (faux GitHub local) ; jamais pose par le template.
  API="${TAVERNE_GITHUB_API:-https://api.github.com}/repos/$DEPOT"
  [ -n "$JETON" ] || echec "le depot $DEPOT est prive : colle un jeton de lecture dans le champ 'Jeton de lecture des versions'."
  command -v jq >/dev/null 2>&1 || echec "l'outil jq manque dans l'image du conteneur."
  if [ -n "$VOULUE" ]; then QUOI="releases/tags/v$VOULUE"; else QUOI="releases/latest"; fi
  etape "recherche de la version ${VOULUE:-la plus recente} sur GitHub ($DEPOT)..."
  code=$(http_code "$API/$QUOI" "$REP" -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28')
  [ "$code" = 200 ] || expliquer "$code" "GitHub ($DEPOT, $QUOI)"
  TAG=$(jq -r '.tag_name // empty' "$REP")
  V_TROUVEE="${TAG#v}"
  [[ "$V_TROUVEE" =~ $VERSION_RE ]] || echec "GitHub : la release '$TAG' n'a pas un numero de version lisible (vX.Y.Z)."
  NOM="taverne-$V_TROUVEE.tar.gz"
  ID=$(jq -r --arg n "$NOM" '.assets[] | select(.name == $n) | .id' "$REP")
  ID_SHA=$(jq -r --arg n "$NOM.sha256" '.assets[] | select(.name == $n) | .id' "$REP")
  DIGEST=$(jq -r --arg n "$NOM" '.assets[] | select(.name == $n) | .digest // empty' "$REP")
  DIGEST="${DIGEST#sha256:}"
  [[ "$ID" =~ ^[0-9]+$ ]] || echec "GitHub : la release $TAG n'a pas de fichier $NOM."
  [[ "$ID_SHA" =~ ^[0-9]+$ ]] || echec "GitHub : la release $TAG n'a pas de fichier $NOM.sha256 (empreinte)."
  URL_ARCHIVE="$API/releases/assets/$ID"
  URL_SHA="$API/releases/assets/$ID_SHA"
  ACCEPT=(-H 'Accept: application/octet-stream')
elif [[ "$SOURCE" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?$ ]] \
  || [[ "$SOURCE" =~ ^http://(127\.0\.0\.1|localhost|172\.17\.0\.1|host\.docker\.internal)(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?$ ]]; then
  BASE="${SOURCE%/}"
  if [ -n "$VOULUE" ]; then
    V_TROUVEE="$VOULUE"
  else
    etape "recherche de la derniere version ($BASE/DERNIERE)..."
    code=$(http_code "$BASE/DERNIERE" "$REP")
    [ "$code" = 200 ] || expliquer "$code" "dossier des versions ($BASE/DERNIERE)"
    V_TROUVEE=$(tr -d '[:space:]' < "$REP")
    [[ "$V_TROUVEE" =~ $VERSION_RE ]] || echec "$BASE/DERNIERE ne contient pas un numero de version."
  fi
  NOM="taverne-$V_TROUVEE.tar.gz"
  URL_ARCHIVE="$BASE/$NOM"
  URL_SHA="$BASE/$NOM.sha256"
  ACCEPT=()
else
  echec "Adresse des versions '$SOURCE' : github:proprietaire/depot, ou une adresse https:// (http:// seulement en local)."
fi

# --- 3. Empreinte attendue, puis l'archive -----------------------------------------------------
SHA_F=".cache/$NOM.sha256"
code=$(http_code "$URL_SHA" "$SHA_F" "${ACCEPT[@]}")
[ "$code" = 200 ] || expliquer "$code" "empreinte de la version $V_TROUVEE"
SHA_PUBLIEE=$(awk 'NR == 1 { print tolower($1) }' "$SHA_F")
rm -f "$SHA_F"
[[ "$SHA_PUBLIEE" =~ ^[0-9a-f]{64}$ ]] || echec "le fichier $NOM.sha256 ne contient pas une empreinte SHA-256."
if [ -n "$DIGEST" ] && [ "$DIGEST" != "$SHA_PUBLIEE" ]; then
  echec "l'empreinte publiee ($NOM.sha256) ne correspond pas a celle calculee par GitHub : rien n'est installe."
fi

if [ -f jeu/.empreinte ] && [ "$(cat jeu/.empreinte)" = "$SHA_PUBLIEE" ] && [ -f jeu/serveur/src/index.js ]; then
  echo "La Taverne $V_TROUVEE est deja installee (empreinte ${SHA_PUBLIEE:0:12}...) : rien a faire."
  exit 0
fi

etape "telechargement de La Taverne $V_TROUVEE..."
ARCHIVE=".cache/$NOM"
code=$(http_code "$URL_ARCHIVE" "$ARCHIVE" "${ACCEPT[@]}")
[ "$code" = 200 ] || { rm -f "$ARCHIVE"; expliquer "$code" "archive $NOM"; }
OBTENUE=$(sha256sum "$ARCHIVE" | cut -d' ' -f1)
if [ "$OBTENUE" != "$SHA_PUBLIEE" ]; then
  rm -f "$ARCHIVE"
  echec "empreinte SHA-256 de $NOM incorrecte (attendue ${SHA_PUBLIEE:0:12}..., obtenue ${OBTENUE:0:12}...) : rien n'est installe."
fi
echo "Empreinte SHA-256 verifiee : $SHA_PUBLIEE"

# --- 4. Installation a cote, verification, puis echange --------------------------------------
rm -rf jeu.new && mkdir -p jeu.new
tar -xzf "$ARCHIVE" -C jeu.new --strip-components=1 --no-same-owner --no-same-permissions \
  || { rm -rf jeu.new "$ARCHIVE"; echec "archive $NOM illisible."; }
rm -f "$ARCHIVE"
[ "$(cat jeu.new/VERSION 2>/dev/null | tr -d '[:space:]')" = "$V_TROUVEE" ] || { rm -rf jeu.new; echec "l'archive ne contient pas la version $V_TROUVEE (fichier VERSION)."; }
( cd jeu.new && sha256sum --quiet --strict -c EMPREINTES.sha256 ) || { rm -rf jeu.new; echec "un fichier de l'archive ne correspond pas a EMPREINTES.sha256 : rien n'est installe."; }
for f in serveur/src/index.js serveur/node_modules/ws/package.json moteur/src/index.js client/index.html; do
  [ -f "jeu.new/$f" ] || { rm -rf jeu.new; echec "fichier $f absent de la version $V_TROUVEE."; }
done
node/bin/node --check jeu.new/serveur/src/index.js || { rm -rf jeu.new; echec "le serveur de la version $V_TROUVEE ne se lit pas avec Node.js $NODE_V."; }

AVANT=$(cat jeu/VERSION 2>/dev/null | tr -d '[:space:]')
if [ -n "$(ls -A donnees 2>/dev/null)" ]; then
  COPIE="sauvegardes/donnees-avant-${V_TROUVEE}-$(date -u +%Y%m%d-%H%M%S).tar.gz"
  tar -czf "$COPIE" donnees || echec "copie de surete de donnees/ impossible : rien n'est change."
  echo "Copie de surete des donnees : $COPIE"
  ls -1t sauvegardes/donnees-avant-*.tar.gz 2>/dev/null | tail -n +6 | while read -r vieux; do rm -f "$vieux"; done
fi
rm -rf jeu.ancien
[ -d jeu ] && mv jeu jeu.ancien
mv jeu.new jeu || echec "echange des dossiers impossible."
echo "$SHA_PUBLIEE" > jeu/.empreinte
echo "TeamKit : La Taverne $V_TROUVEE installee${AVANT:+ (a la place de $AVANT, gardee dans jeu.ancien/)}. Donnees gardees : donnees/ ($(du -sh donnees | cut -f1))."
