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
#  3. laisse la place au serveur (exec) : le SIGTERM d'AMP lui arrive directement, il
#     sauvegarde les tables en cours dans donnees/ et s'arrete (au plus 15 s).
# Le serveur ecrit lui-meme la ligne 'Pret', les arrivees et les departs (regex d'AMP).
cd "$(dirname "$0")" || exit 1
ICI="$PWD"
NODE="$ICI/node/bin/node"
JEU="$ICI/jeu"
export LANG=C.UTF-8 LC_ALL=C.UTF-8

# Seulement les cles connues, chacune une fois : un guillemet colle dans un reglage d'AMP
# (remplacement brut) ne peut pas glisser un argument de plus.
CONNUES=" port url origines proxy pendule signes parties stats moderation connexions parip tables "
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
ADRESSE_RE='^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$'
[[ "$URL" =~ $ADRESSE_RE ]] || refuser "adresse publique '$URL' : de la forme https://taverne.teamkit.fr (sans chemin ni / a la fin)."
[ -n "$ORIGINES" ] || refuser "origines autorisees vides : mets au moins $URL."
IFS=',' read -r -a LISTE <<< "$ORIGINES"
for o in "${LISTE[@]}"; do
  [[ "$o" =~ $ADRESSE_RE ]] || refuser "origine '$o' : de la forme https://taverne.teamkit.fr, separees par des virgules."
done

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
export TAVERNE_CLE_JETON="$CLE"

echo "TeamKit : La Taverne $(tr -d '[:space:]' < "$JEU/VERSION") avec Node.js $("$NODE" --version), port $PORT, adresse $URL"
cd "$JEU/serveur" || exit 1
exec "$NODE" src/index.js 2>&1
