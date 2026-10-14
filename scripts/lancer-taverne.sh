#!/bin/bash
# lancer-taverne.sh - pose par le template AMP TeamKit (recree a chaque Update).
#
#   lancer-taverne.sh cle=valeur ...     demarrage du jeu (ligne de commande d'AMP)
#
# AMP passe chaque reglage sous la forme cle=valeur, entre guillemets : un reglage vide
# reste un argument "cle=" et ne decale jamais les autres. La cle des jetons arrive par la
# variable d'environnement TAVERNE_CLE_JETON (App.EnvironmentVariables) : elle n'est
# jamais sur la ligne de commande ni dans la console.
#
# Ce script :
#  1. controle chaque reglage et refuse de demarrer avec un message clair si l'un est faux
#     (cle des jetons vide, trop courte ou d'exemple ; adresse ; bornes) ;
#  2. donne les reglages au serveur en variables d'environnement (celles de .env.example) ;
#  3. lance le serveur et lui passe le SIGTERM d'AMP : il sauvegarde les tables en cours dans
#     donnees/ et s'arrete (au plus 15 s) ; la console d'AMP arrive a son entree (commandes
#     "maintenance 10", "maintenance annuler"...) ;
#  4. mise a jour en douceur : quand le serveur sort avec le code 75 (fin du compte a rebours
#     de "maintenance"), le lanceur fait l'Update du jeu (installer-taverne.sh, meme source,
#     meme jeton, meme version voulue) puis relance le serveur, sans arreter l'instance.
#     Une Update ratee ne bloque rien : la version en place repart.
# Le jeton de lecture arrive par TAVERNE_JETON_VERSIONS (App.EnvironmentVariables) : il est
# retire de l'environnement avant de lancer le jeu, qui n'en a pas besoin.
# Vocal de la table : la cle et le secret du serveur vocal arrivent par TAVERNE_VOIX_CLE et
# TAVERNE_VOIX_SECRET (App.EnvironmentVariables). Reglages du vocal vides ou faux = le jeu
# demarre quand meme, SANS vocal integre (un avertissement le dit) : le vocal ne bloque jamais.
# Le serveur ecrit lui-meme la ligne 'Pret', les arrivees et les departs (regex d'AMP).
cd "$(dirname "$0")" || exit 1
ICI="$PWD"
NODE="$ICI/node/bin/node"
JEU="$ICI/jeu"
export LANG=C.UTF-8 LC_ALL=C.UTF-8

# Seulement les cles connues, chacune une fois : un guillemet colle dans un reglage d'AMP
# (remplacement brut) ne peut pas glisser un argument de plus.
CONNUES=" port url origines proxy pendule signes parties stats moderation connexions parip tables annonce versions voulue voix discord site "
declare -A V
for a in "$@"; do
  case "$a" in
    *=*) k="${a%%=*}" ;;
    *) echo "ERREUR : argument inattendu : $a"; exit 1 ;;
  esac
  [[ "$CONNUES" == *" $k "* ]] || { echo "ERREUR : reglage inattendu '$k' (un guillemet dans un reglage d'AMP ?)."; exit 1; }
  [ -z "${V[$k]+x}" ] || { echo "ERREUR : reglage '$k' en double (un guillemet dans un reglage d'AMP ?)."; exit 1; }
  V["$k"]="${a#*=}"
done

refuser() {
  echo "ERREUR : $1"
  echo "Corrige ce reglage dans l'onglet Configuration d'AMP (rubrique La Taverne), puis relance."
  exit 1
}
borne() {
  [[ "$2" =~ ^[0-9]+$ ]] && [ "$2" -ge "$3" ] && [ "$2" -le "$4" ] \
    || refuser "$1 : '$2' n'est pas un nombre entre $3 et $4."
}
oui_non() { [ "$1" = "oui" ] && echo 1 || echo 0; }

# --- 1. La cle des jetons -------------------------------------------------------------
CLE="${TAVERNE_CLE_JETON:-}"
probleme=""
if [ -z "$CLE" ]; then
  probleme="la cle des jetons (TAVERNE_CLE_JETON) est vide"
elif [ "${#CLE}" -lt 32 ]; then
  probleme="la cle des jetons (TAVERNE_CLE_JETON) est trop courte : ${#CLE} caracteres, il en faut au moins 32"
elif [[ "$CLE" == remplacer-par-* ]]; then
  probleme="la cle des jetons (TAVERNE_CLE_JETON) est encore celle de l'exemple"
elif [[ ! "$CLE" =~ ^[A-Za-z0-9_+/=.-]+$ ]]; then
  probleme="la cle des jetons (TAVERNE_CLE_JETON) contient un espace, un guillemet ou un caractere special"
fi
if [ -n "$probleme" ]; then
  echo "ERREUR : $probleme : La Taverne refuse de demarrer."
  echo "  1. Fabrique une cle (sur le dedie ou dans un terminal) : openssl rand -hex 32"
  echo "  2. Colle-la dans AMP : onglet Configuration, rubrique La Taverne, champ 'Cle des jetons'."
  echo "  3. Colle LA MEME dans le panel TeamKit : Reglages, Site, La Taverne."
  echo "  4. Relance l'instance."
  exit 1
fi

# --- 2. Les autres reglages ------------------------------------------------------------
PORT="${V[port]}"
URL="${V[url]%/}"
ORIGINES="${V[origines]// /}"
PROXY="${V[proxy]:-oui}"
PENDULE="${V[pendule]:-30000}"
SIGNES="${V[signes]:-oui}"
PARTIES="${V[parties]:-30}"
STATS="${V[stats]:-365}"
MODERATION="${V[moderation]:-365}"
CONNEXIONS="${V[connexions]:-1000}"
PARIP="${V[parip]:-20}"
TABLES="${V[tables]:-500}"
ANNONCE="${V[annonce]:-10}"
SOURCE_V="${V[versions]// /}"
VOULUE_V="${V[voulue]// /}"
JETON_V="${TAVERNE_JETON_VERSIONS:-}"
unset TAVERNE_JETON_VERSIONS
VOIX_URL="${V[voix]// /}"
VOIX_URL="${VOIX_URL%/}"
DISCORD="${V[discord]:-non}"
SITE="${V[site]// /}"
SITE="${SITE%/}"
[ -n "$SITE" ] || SITE="https://www.teamkit.fr"
VOIX_K="${TAVERNE_VOIX_CLE:-}"
VOIX_S="${TAVERNE_VOIX_SECRET:-}"
unset TAVERNE_VOIX_CLE TAVERNE_VOIX_SECRET

borne "port" "$PORT" 1024 65535
borne "parties gardees" "$PARTIES" 1 365
borne "statistiques gardees" "$STATS" 30 1095
borne "journal de moderation garde" "$MODERATION" 30 1095
borne "connexions au plus" "$CONNEXIONS" 10 20000
borne "connexions par adresse IP" "$PARIP" 1 200
borne "tables au plus" "$TABLES" 10 5000
[[ "$PENDULE" =~ ^(0|15000|30000|60000|120000)$ ]] || refuser "temps par coup '$PENDULE' inconnu."
[[ "$PROXY" =~ ^(oui|non)$ ]] || refuser "derriere nginx '$PROXY' : oui ou non."
[[ "$SIGNES" =~ ^(oui|non)$ ]] || refuser "signes des bots '$SIGNES' : oui ou non."
[[ "$ANNONCE" =~ ^(5|10|15)$ ]] || refuser "delai d'annonce '$ANNONCE' : 5, 10 ou 15 minutes."
ADRESSE_RE='^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$'
[[ "$URL" =~ $ADRESSE_RE ]] || refuser "adresse publique '$URL' : de la forme https://taverne.teamkit.fr (sans chemin ni / a la fin)."
[ -n "$ORIGINES" ] || refuser "origines autorisees vides : mets au moins $URL."
IFS=',' read -r -a LISTE <<< "$ORIGINES"
for o in "${LISTE[@]}"; do
  [[ "$o" =~ $ADRESSE_RE ]] || refuser "origine '$o' : de la forme https://taverne.teamkit.fr, separees par des virgules."
done
[[ "$DISCORD" =~ ^(oui|non)$ ]] || refuser "salon Discord de la table '$DISCORD' : oui ou non."
[[ "$SITE" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]] || refuser "adresse du site TeamKit '$SITE' : de la forme https://www.teamkit.fr (sans chemin ni / a la fin)."
# Vocal integre : les trois reglages ensemble, sinon pas de vocal integre (le jeu demarre quand meme).
VOIX_OK=0
if [ -z "$VOIX_URL" ] && [ -z "$VOIX_K" ] && [ -z "$VOIX_S" ]; then
  echo "TeamKit : vocal integre absent (reglages du vocal vides)."
elif ! [[ "$VOIX_URL" =~ ^wss://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]]; then
  echo "AVERTISSEMENT : vocal integre coupe : l'adresse du vocal doit etre de la forme wss://voix.teamkit.fr (sans chemin ni / a la fin)."
elif ! [[ "$VOIX_K" =~ ^[A-Za-z0-9_-]{3,64}$ ]]; then
  echo "AVERTISSEMENT : vocal integre coupe : la cle du vocal (API key) est vide ou contient un caractere special."
elif ! [[ "$VOIX_S" =~ ^[A-Za-z0-9_+/=-]{32,128}$ ]]; then
  echo "AVERTISSEMENT : vocal integre coupe : le secret du vocal est vide, trop court (32 caracteres au moins) ou contient un caractere special."
else
  VOIX_OK=1
  echo "TeamKit : vocal integre branche sur $VOIX_URL."
fi

# --- 3. Le jeu est-il installe ? -------------------------------------------------------
[ -x "$NODE" ] || { echo "ERREUR : Node.js n'est pas installe ($NODE). Lance Update."; exit 1; }
[ -f "$JEU/serveur/src/index.js" ] && [ -f "$JEU/VERSION" ] \
  || { echo "ERREUR : La Taverne n'est pas installee ($JEU). Regle 'Adresse des versions' et 'Jeton de lecture', puis lance Update."; exit 1; }
mkdir -p "$ICI/donnees" || exit 1

export NODE_ENV=production
export PORT
export TAVERNE_HOTE=0.0.0.0
export TAVERNE_URL_PUBLIQUE="$URL"
export TAVERNE_ORIGINES="$ORIGINES"
export TAVERNE_PROXY="$(oui_non "$PROXY")"
export TAVERNE_CLIENT="$JEU/client"
export TAVERNE_MOTEUR="$JEU/moteur/src"
export TAVERNE_STOCKAGE=fichiers
export TAVERNE_DONNEES="$ICI/donnees"
export TAVERNE_PENDULE_MS="$PENDULE"
export TAVERNE_SIGNES_BOTS="$(oui_non "$SIGNES")"
export TAVERNE_GARDER_PARTIES_JOURS="$PARTIES"
export TAVERNE_GARDER_STATS_JOURS="$STATS"
export TAVERNE_GARDER_MODERATION_JOURS="$MODERATION"
export TAVERNE_MAX_CONNEXIONS="$CONNEXIONS"
export TAVERNE_MAX_CONNEXIONS_IP="$PARIP"
export TAVERNE_MAX_TABLES="$TABLES"
export TAVERNE_JOURNAL_JOUEURS=1
export TAVERNE_MAINTENANCE_MIN="$ANNONCE"
export TAVERNE_CLE_JETON="$CLE"
export TAVERNE_SALON_DISCORD="$(oui_non "$DISCORD")"
export TAVERNE_SITE_URL="$SITE"
if [ "$VOIX_OK" = 1 ]; then
  export TAVERNE_VOIX_URL="$VOIX_URL"
  export TAVERNE_VOIX_CLE="$VOIX_K"
  export TAVERNE_VOIX_SECRET="$VOIX_S"
fi
unset VOIX_K VOIX_S

# --- 4. Le jeu, et sa relance apres une mise a jour annoncee (code 75) ------------------
RELANCE=75
ARRET=0
ENFANT=""
arreter() { ARRET=1; [ -n "$ENFANT" ] && kill -TERM "$ENFANT" 2>/dev/null; }
trap arreter TERM INT
# L'entree du lanceur (console d'AMP) va au jeu : un programme lance en arriere-plan aurait /dev/null.
exec 3<&0
attendre() { wait "$ENFANT"; CODE=$?; while kill -0 "$ENFANT" 2>/dev/null; do wait "$ENFANT"; CODE=$?; done; ENFANT=""; }
while :; do
  [ -x "$NODE" ] && [ -f "$JEU/serveur/src/index.js" ] && [ -f "$JEU/VERSION" ] \
    || { echo "ERREUR : La Taverne n'est plus installee apres la mise a jour ($JEU). Lance Update."; exit 1; }
  echo "TeamKit : La Taverne $(tr -d '[:space:]' < "$JEU/VERSION") avec Node.js $("$NODE" --version), port $PORT, adresse $URL"
  cd "$JEU/serveur" || exit 1
  "$NODE" src/index.js <&3 2>&1 &
  ENFANT=$!
  attendre
  cd "$ICI" || exit 1
  [ "$ARRET" = 1 ] && exit 0
  [ "$CODE" = "$RELANCE" ] || exit "$CODE"
  echo "TeamKit : mise a jour demandee par le jeu (code $RELANCE) : Update de La Taverne, puis relance..."
  if [ -z "$SOURCE_V" ]; then
    echo "TeamKit : 'Adresse des versions' est vide : pas d'Update, la version en place repart."
  elif [ ! -f "$ICI/installer-taverne.sh" ]; then
    echo "TeamKit : installer-taverne.sh absent (lance Update dans AMP une fois) : la version en place repart."
  else
    /bin/bash "$ICI/installer-taverne.sh" "source=$SOURCE_V" "jeton=$JETON_V" "version=$VOULUE_V" </dev/null 2>&1 &
    ENFANT=$!
    attendre
    [ "$ARRET" = 1 ] && exit 0
    [ "$CODE" = 0 ] || echo "TeamKit : l'Update n'a pas abouti (code $CODE) : la version en place repart. Verifie le jeton de lecture puis relance 'maintenance'."
  fi
done
