# -*- coding: utf-8 -*-
"""
Contrôle du template avant de le pousser (même rôle que ceux de Scribblers et d'Alien Swarm RD).

    python verifier.py

1. tous les JSON se parsent ;
2. le .kvp : fichiers inclus, bloc Console.* / Limits.* complet, image Debian, conteneur obligatoire,
   arrêt par SIGTERM, même dépôt que le manifest, identifiants neufs (absents des autres templates TeamKit) ;
3. le port : un seul, TCP, 8790, hors des ports d'AMP (8080-8125) ;
4. les réglages : rubriques sans « : » dans le nom, chaque réglage passé une fois (ligne de commande,
   environnement ou Update), les deux secrets en mot de passe, exclus de l'import, jamais sur la ligne
   de commande du jeu ; les clés passées = celles que lit le lanceur ;
5. les fichiers téléchargés à l'Update existent dans scripts/, à l'URL du dépôt du manifest ;
6. les scripts : LF, ASCII, aucun {{, `bash -n` ; Node.js 22 figé avec son empreinte ;
7. aller-retour par le VRAI lanceur sous bash (Node remplacé par un bouchon) : réglages rendus comme AMP,
   variables données au jeu, refus clairs (clé vide, courte, d'exemple ; adresse ; bornes), console d'AMP
   transmise au jeu, jeton de lecture retiré du jeu, relance avec Update quand le jeu sort avec le code 75 ;
8. le VRAI installateur sous bash contre un faux dossier de versions local protégé par un jeton : installation,
   « déjà installée », mauvais jeton, empreinte fausse, fichier modifié dans l'archive, mise à jour (ancienne
   gardée, copie des données), retour à une version précise, données jamais touchées ;
9. les regex de console d'AMP sur de vraies lignes du serveur (essai du 13/10/2026), lignes de maintenance comprises ;
10. aucun secret dans le dépôt.
"""
import glob
import hashlib
import http.server
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading

ICI = os.path.dirname(os.path.abspath(__file__))
NOM = 'taverne'
LANCEUR = 'scripts/lancer-taverne.sh'
INSTALLEUR = 'scripts/installer-taverne.sh'
soucis = []


def lire(nom, binaire=False):
    with open(os.path.join(ICI, nom), 'rb') as f:
        brut = f.read()
    return brut if binaire else brut.decode('utf-8')


def ok(quoi, detail):
    print('ok   %-26s %s' % (quoi, detail))


def py(motif):
    # AMP est en C# : (?<nom>...) ; Python veut (?P<nom>...)
    return re.compile(motif.replace('(?<', '(?P<'))


# ── 1. JSON ──────────────────────────────────────────────────────────────────
data = {}
for nom in ('manifest.json', NOM + 'config.json', NOM + 'metaconfig.json', NOM + 'ports.json', NOM + 'updates.json'):
    try:
        data[nom] = json.loads(lire(nom))
        ok(nom, 'JSON valide')
    except Exception as e:
        soucis.append('%s : JSON invalide (%s)' % (nom, e))
if soucis:
    print('\n'.join('NON  ' + s for s in soucis))
    sys.exit(1)
config = data[NOM + 'config.json']
updates = data[NOM + 'updates.json']
ports = data[NOM + 'ports.json']
manifest = data['manifest.json']
lanceur = lire(LANCEUR)
installeur = lire(INSTALLEUR)

# ── 2. kvp ───────────────────────────────────────────────────────────────────
kvp = lire(NOM + '.kvp')
cles = dict(l.split('=', 1) for l in kvp.splitlines() if '=' in l)
for ref in re.findall(r'@IncludeJson\[([^\]]+)\]', kvp):
    if not os.path.isfile(os.path.join(ICI, ref)):
        soucis.append('@IncludeJson[%s] : fichier absent' % ref)
for cle in ('Meta.ConfigManifest', 'Meta.MetaConfigManifest', 'Meta.ConfigRoot'):
    if not os.path.isfile(os.path.join(ICI, cles.get(cle, '?'))):
        soucis.append('%s pointe sur un fichier absent' % cle)
ATTENDUES = ['App.SupportsUniversalSleep', 'App.WakeupMode', 'App.ApplicationReadyMode', 'Console.FilterMatchRegex',
             'Console.FilterMatchReplacement', 'Console.ThrowawayMessageRegex', 'Console.AppReadyRegex',
             'Console.UserJoinRegex', 'Console.UserLeaveRegex', 'Console.UserChatRegex', 'Console.UpdateAvailableRegex',
             'Console.PreConnectRegex', 'Console.ConnectIPRegex', 'Console.MetricsRegex', 'Console.HideFromConsoleRegex',
             'Console.SuppressLogAtStart', 'Console.UserActions', 'Limits.SleepMode', 'Limits.SleepOnStart',
             'Limits.SleepDelayMinutes', 'Limits.DozeDelay', 'Limits.AutoRetryCount', 'Limits.SleepStartThresholdSeconds']
absentes = [c for c in ATTENDUES if c not in cles]
if absentes:
    soucis.append('clés absentes du .kvp : ' + ', '.join(absentes))
else:
    ok('kvp complet', 'les %d clés Console/Limits sont là' % len(ATTENDUES))
ATTENDU_KVP = {
    'Meta.SpecificDockerImage': 'cubecoders/ampbase:debian',
    'Meta.ContainerPolicy': 'Required',
    'Meta.DockerRequired': 'True',
    'Meta.ExtraContainerPackages': '[]',
    'Meta.EndpointURIFormat': 'https://taverne.teamkit.fr',
    'App.ExecutableLinux': '/bin/bash',
    'App.ExitMethod': 'SIGTERM',               # le serveur sauvegarde les tables puis s'arrête (15 s au plus)
    'App.HasReadableConsole': 'True',
    'App.HasWriteableConsole': 'True',          # « maintenance 10 » tapé dans la console d'AMP (ou envoyé par le planificateur)
    'App.AdminMethod': 'STDIO',
    'App.ApplicationReadyMode': 'RegexMatch',
    'App.RootDir': './taverne/',
    'App.BaseDirectory': './taverne/',
    'App.PrimaryApplicationPortRef': 'HttpPort',
    'App.EnvironmentVariables': '{"TAVERNE_CLE_JETON":"{{CleJeton}}","TAVERNE_JETON_VERSIONS":"{{JetonVersions}}"}',
    'Limits.SleepMode': 'False',
}
for cle, val in ATTENDU_KVP.items():
    if cles.get(cle) != val:
        soucis.append('%s devrait valoir %s (vaut %r)' % (cle, val, cles.get(cle)))
if not 1 <= int(cles.get('Limits.AutoRetryCount', '0')) <= 3:
    soucis.append('Limits.AutoRetryCount : 1 à 3 relances après un plantage (0 couperait toute relance automatique)')
if int(cles.get('App.ExitTimeout', '0')) < 20:
    soucis.append('App.ExitTimeout doit laisser au serveur ses 15 s d\'arrêt propre (>= 20)')
if cles.get('Meta.URL') != manifest['url'] or manifest['origin'] != manifest['url'] + '.git':
    soucis.append('Meta.URL et manifest.json ne désignent pas le même dépôt')
if manifest.get('authors') != ['Hydrocut', 'TeamKit'] or not manifest.get('prefix', '').startswith('TeamKit'):
    soucis.append('manifest : auteurs Hydrocut + TeamKit, prefix TeamKit…')
autres = [p for p in glob.glob(os.path.join(ICI, '..', 'AMPTemplates-*', '*'))
          if os.path.abspath(os.path.dirname(p)) != ICI and p.endswith(('.kvp', 'manifest.json'))]
deja = ''
for p in autres:
    if os.path.isfile(p):
        deja += io.open(p, encoding='utf-8', errors='replace').read()
for quoi, val in (('Meta.AppConfigId', cles.get('Meta.AppConfigId', '')), ('id du manifest', manifest['id'])):
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', val):
        soucis.append('%s : UUID v4 attendu (%r)' % (quoi, val))
    elif val in deja:
        soucis.append('%s déjà utilisé par un autre template TeamKit' % quoi)
if cles.get('Meta.AppConfigId') == manifest['id']:
    soucis.append('AppConfigId et id du manifest doivent différer')
ok('kvp', 'Debian, conteneur obligatoire, SIGTERM, %d autres templates lus' % len(autres))

# ── 3. port ──────────────────────────────────────────────────────────────────
if (len(ports) != 1 or ports[0]['Protocol'] != 'TCP' or ports[0]['Ref'] != 'HttpPort' or ports[0]['Port'] != 8790):
    soucis.append('port : un seul, HttpPort 8790 en TCP')
elif 8080 <= ports[0]['Port'] <= 8125:
    soucis.append('port dans la plage des panneaux d\'AMP')
else:
    ok('port', '8790 TCP (page + WebSocket), passé au lanceur')

# ── 4. réglages ──────────────────────────────────────────────────────────────
for c in config:
    for cle, n in (('Category', 1), ('Subcategory', 2)):
        if c.get(cle) and c[cle].count(':') != n:
            soucis.append('%s : %s %r doit avoir exactement %d « : »' % (c['FieldName'], cle, c[cle], n))
    if c.get('IncludeInCommandLine') is not False:
        soucis.append('%s : IncludeInCommandLine doit être false' % c['FieldName'])
par_nom = {c['ParamFieldName']: c for c in config}
if len(par_nom) != len(config) or len({c['FieldName'] for c in config}) != len(config):
    soucis.append('réglages en double')
ligne = cles['App.CommandLineArgs']
args_maj = updates[-1]['UpdateSourceArgs']
env_kvp = cles['App.EnvironmentVariables']
passes = re.findall(r'"([a-z]+)=\{\{([$A-Za-z0-9_]+)\}\}"', ligne)
# Les 3 réglages de l'Update servent aussi au lanceur (relance avec Update après « maintenance », code 75) :
# passés une fois à l'Update ET une fois au lanceur (le jeton par l'environnement, jamais sur la ligne de commande).
DE_L_UPDATE = ('AdresseVersions', 'JetonVersions', 'VersionVoulue')
for c in config:
    champ = c['FieldName']
    nb = ligne.count('{{%s}}' % champ) + args_maj.count('{{%s}}' % champ) + env_kvp.count('{{%s}}' % champ)
    attendu = 2 if champ in DE_L_UPDATE else 1
    if nb != attendu or (champ in DE_L_UPDATE and args_maj.count('{{%s}}' % champ) != 1):
        soucis.append('{{%s}} doit être passé %s : %d' % (champ, 'à l\'Update et au lanceur' if attendu == 2 else 'exactement une fois (ligne de commande, Update ou environnement)', nb))
SECRETS = ('CleJeton', 'JetonVersions')
for s in SECRETS:
    c = par_nom[s]
    if c['InputType'] != 'password' or not c.get('ExcludeFromImport') or c['DefaultValue'] != '':
        soucis.append('%s : mot de passe, exclu de l\'import, vide par défaut' % s)
    if '{{%s}}' % s in ligne:
        soucis.append('%s ne doit jamais être sur la ligne de commande du jeu' % s)
if '{{CleJeton}}' not in env_kvp or '{{JetonVersions}}' not in args_maj or '"TAVERNE_JETON_VERSIONS":"{{JetonVersions}}"' not in env_kvp:
    soucis.append('la clé passe par l\'environnement, le jeton de lecture par l\'Update et par l\'environnement du lanceur')
if not re.search(r'^unset TAVERNE_JETON_VERSIONS$', lanceur, re.M) or lanceur.index('unset TAVERNE_JETON_VERSIONS') > lanceur.index('src/index.js <&3'):
    soucis.append('le lanceur doit retirer TAVERNE_JETON_VERSIONS de l\'environnement avant de lancer le jeu')
lues = set(re.findall(r'\$\{V\[(\w+)\]', lanceur))
if {k for k, _ in passes} != lues:
    soucis.append('clés passées %s ≠ clés lues par le lanceur %s' % (sorted({k for k, _ in passes}), sorted(lues)))
if set(re.findall(r'\$\{V\[(\w+)\]', installeur)) != set(re.findall(r'"([a-z]+)=\{\{', args_maj)):
    soucis.append('clés de l\'Update ≠ clés lues par l\'installateur')
if data[NOM + 'metaconfig.json'] != []:
    soucis.append('metaconfig : aucun fichier rendu par AMP (tout passe par le lanceur)')
if not [s for s in soucis if 'réglage' in s or '{{' in s or 'clé' in s]:
    ok('réglages', '%d réglages, lanceur et installateur d\'accord, 2 secrets protégés' % len(config))

# ── 5. fichiers de l'Update ───────────────────────────────────────────────────
brut = 'https://raw.githubusercontent.com/' + manifest['url'].split('github.com/')[1] + '/main/'
for u in updates:
    if u['UpdateSource'] == 'FetchURL':
        url = u['UpdateSourceData']
        if not url.startswith(brut + 'scripts/') or not os.path.isfile(os.path.join(ICI, url[len(brut):])):
            soucis.append('Update : %s ne pointe pas sur un fichier de scripts/ de ce dépôt' % url)
        if u['UpdateSourceArgs'] != url.rsplit('/', 1)[1] or u['UpdateSourceTarget'] != '{{$FullBaseDir}}':
            soucis.append('Update : %s mal rangé' % url)
if updates[-1]['UpdateSource'] != 'Executable' or updates[-1]['UpdateSourceData'] != '/bin/bash':
    soucis.append('dernière étape de l\'Update : /bin/bash installer-taverne.sh')
ok('Update', '%d étapes, scripts de ce dépôt' % len(updates))

# ── 6. scripts ───────────────────────────────────────────────────────────────
for s in (LANCEUR, INSTALLEUR):
    b = lire(s, True)
    if b'\r' in b:
        soucis.append('%s : fins de ligne CRLF (bash les refuse)' % s)
    try:
        b.decode('ascii')
    except UnicodeDecodeError:
        soucis.append('%s : caractères non ASCII' % s)
    if b'{{' in b:
        soucis.append('%s : « {{ » serait remplacé par AMP' % s)
m = re.search(r'^NODE_V=v(\d+)\.(\d+)\.(\d+)$', installeur, re.M)
if not m or m.group(1) != '22':
    soucis.append('Node.js 22 doit être figé dans l\'installateur (NODE_V=v22.x.y)')
if not re.search(r'^NODE_SHA=[0-9a-f]{64}$', installeur, re.M):
    soucis.append('empreinte SHA-256 de Node.js absente de l\'installateur')


def trouver_bash():
    for c in (r'C:\Program Files\Git\bin\bash.exe', r'C:\Program Files\Git\usr\bin\bash.exe', '/bin/bash'):
        if os.path.isfile(c):
            return c
    c = shutil.which('bash')
    if c and 'system32' not in c.lower():   # le bash de WSL ne verrait pas les chemins Windows
        return c
    return None


bash = trouver_bash()
if bash:
    for s in (LANCEUR, INSTALLEUR):
        r = subprocess.run([bash, '-n', os.path.join(ICI, s)], capture_output=True, text=True)
        if r.returncode:
            soucis.append('bash -n %s : %s' % (s, r.stderr.strip()))
    ok('scripts', 'LF, ASCII, bash -n, Node %s figé' % ('.'.join(m.groups()) if m else '?'))


def unix(p):
    return p.replace('\\', '/')


def rendre(modele, valeurs):
    """Remplacement brut des {{Réglage}} comme AMP, puis découpe en arguments entre guillemets."""
    for k, v in valeurs.items():
        modele = modele.replace('{{%s}}' % k, v)
    return re.findall(r'"([^"]*)"', modele)


# ── 7. aller-retour par le vrai lanceur ─────────────────────────────────────
if not bash:
    print('---  aller-retour              bash introuvable : étapes 7 et 8 sautées (à faire avant de pousser)')
else:
    tmp = tempfile.mkdtemp(prefix='taverne-lanceur-')
    try:
        os.makedirs(os.path.join(tmp, 'node', 'bin'))
        os.makedirs(os.path.join(tmp, 'jeu', 'serveur', 'src'))
        with open(os.path.join(tmp, 'jeu', 'VERSION'), 'w', newline='\n') as f:
            f.write('0.1.0\n')
        with open(os.path.join(tmp, 'jeu', 'serveur', 'src', 'index.js'), 'w') as f:
            f.write('// bouchon\n')
        with open(os.path.join(tmp, 'node', 'bin', 'node'), 'w', newline='\n') as f:
            # Bouchon : écrit ce qu'il reçoit, lit une ligne de la console (AMP), et sort avec le code
            # demandé par le fichier codes.txt (une ligne par lancement : 75 = « relance demandée »).
            f.write('#!/bin/bash\nif [ "$1" = "--version" ]; then echo v22.23.3; exit 0; fi\n'
                    'echo "ARGS=$*"; echo "PWD=$PWD"; env | grep -E "^(NODE_ENV|PORT|TAVERNE_)" | sort\n'
                    'if read -r -t 2 ligne; then echo "CONSOLE=$ligne"; fi\n'
                    'F="$PWD/../../codes.txt"; c=0\n'
                    'if [ -s "$F" ]; then c=$(head -n 1 "$F"); tail -n +2 "$F" > "$F.n"; mv "$F.n" "$F"; fi\n'
                    'echo "SORTIE=$c"; exit "$c"\n')
        shutil.copy(os.path.join(ICI, LANCEUR), tmp)
        defauts = {c['FieldName']: c['DefaultValue'] for c in config}
        defauts.update({'$HttpPort': '8790', '$FullBaseDir': unix(tmp) + '/'})

        def lancer(change=None, cle='0123456789abcdef' * 4, entree=b'', codes=()):
            v = dict(defauts)
            v.update(change or {})
            args = rendre(ligne, v)
            env = dict(os.environ)
            env.pop('TAVERNE_CLE_JETON', None)
            if cle is not None:
                env['TAVERNE_CLE_JETON'] = cle
            env['TAVERNE_JETON_VERSIONS'] = v.get('JetonVersions', '')
            with open(os.path.join(tmp, 'codes.txt'), 'w', newline='\n') as f:
                f.write(''.join('%d\n' % c for c in codes))
            p = subprocess.run([bash] + args, env=env, input=entree, capture_output=True, timeout=60)
            return p.returncode, p.stdout.decode('utf-8', 'replace') + p.stderr.decode('utf-8', 'replace')

        n = 0

        def cas(titre, change, code, dedans=(), dehors=(), cle='0123456789abcdef' * 4, entree=b'', codes=()):
            global n
            n += 1
            c, sortie = lancer(change, cle, entree, codes)
            if c != code:
                soucis.append('lanceur, %s : code %d au lieu de %d\n%s' % (titre, c, code, sortie))
                return
            for d in dedans:
                if d not in sortie:
                    soucis.append('lanceur, %s : « %s » absent\n%s' % (titre, d, sortie))
            for d in dehors:
                if d in sortie:
                    soucis.append('lanceur, %s : « %s » ne devrait pas sortir\n%s' % (titre, d, sortie))

        c0, s0 = lancer()
        for var, fin in (('TAVERNE_CLIENT', '/jeu/client'), ('TAVERNE_MOTEUR', '/jeu/moteur/src'), ('TAVERNE_DONNEES', '/donnees')):
            mv = re.search('^%s=(.*)$' % var, s0, re.M)
            if not mv or not mv.group(1).endswith(os.path.basename(tmp) + fin):
                soucis.append('lanceur : %s devrait finir par %s' % (var, fin))
        cas('défauts', {}, 0, dedans=(
            'ARGS=src/index.js', 'NODE_ENV=production', 'PORT=8790', 'TAVERNE_URL_PUBLIQUE=https://taverne.teamkit.fr',
            'TAVERNE_ORIGINES=https://taverne.teamkit.fr,https://teamkit.fr,https://www.teamkit.fr', 'TAVERNE_PROXY=1',
            'TAVERNE_PENDULE_MS=30000',
            'TAVERNE_SIGNES_BOTS=1', 'TAVERNE_GARDER_PARTIES_JOURS=30', 'TAVERNE_GARDER_STATS_JOURS=365', 'TAVERNE_MAX_CONNEXIONS=1000',
            'TAVERNE_MAX_CONNEXIONS_IP=20', 'TAVERNE_MAX_TABLES=500', 'TAVERNE_JOURNAL_JOUEURS=1', 'TAVERNE_STOCKAGE=fichiers',
            'TAVERNE_MAINTENANCE_MIN=10', 'La Taverne 0.1.0 avec Node.js v22.23.3, port 8790', 'SORTIE=0'),
            dehors=('ERREUR', 'TAVERNE_JETON_VERSIONS', 'mise a jour demandee'))
        # La console d'AMP arrive au jeu (commande « maintenance 10 » du planificateur ou tapée à la main).
        cas('console d\'AMP transmise au jeu', {}, 0, entree=b'maintenance 10\n', dedans=('CONSOLE=maintenance 10',))
        cas('délai d\'annonce 15 min', {'DelaiAnnonce': '15'}, 0, dedans=('TAVERNE_MAINTENANCE_MIN=15',))
        cas('délai d\'annonce inconnu', {'DelaiAnnonce': '7'}, 1, dedans=('delai d\'annonce \'7\'',), dehors=('ARGS=',))
        # Le jeu sort avec le code 75 (fin du compte à rebours) : Update (faux installateur), puis relance.
        with open(os.path.join(tmp, 'installer-taverne.sh'), 'w', newline='\n') as f:
            f.write('#!/bin/bash\nfor a in "$@"; do case "$a" in jeton=?*) echo "INSTALLATEUR jeton recu";; jeton=) echo "INSTALLATEUR sans jeton";; *) echo "INSTALLATEUR $a";; esac; done\n'
                    'env | grep -q TAVERNE_JETON_VERSIONS && echo "FUITE"\n'
                    'exit "$(cat "$(dirname "$0")/code-installateur.txt" 2>/dev/null || echo 0)"\n')
        jeton_v = 'jeton-de-lecture-' + 'cd34' * 4
        cas('code 75 : Update puis relance', {'JetonVersions': jeton_v, 'VersionVoulue': '0.1.0'}, 0, codes=(75, 0), dedans=(
            'mise a jour demandee par le jeu (code 75)', 'INSTALLATEUR source=github:hydrocut/taverne', 'INSTALLATEUR jeton recu',
            'INSTALLATEUR version=0.1.0', 'SORTIE=75', 'SORTIE=0'), dehors=(jeton_v, 'FUITE', 'ERREUR'))
        c75, s75 = lancer({'JetonVersions': jeton_v}, codes=(75, 75, 0))
        if c75 != 0 or s75.count('La Taverne 0.1.0 avec Node.js') != 3:
            soucis.append('lanceur : deux relances de suite devraient lancer le jeu 3 fois (code %d)\n%s' % (c75, s75))
        with open(os.path.join(tmp, 'code-installateur.txt'), 'w') as f:
            f.write('1')
        cas('Update ratée : la version en place repart', {}, 0, codes=(75, 0), dedans=('n\'a pas abouti (code 1)', 'SORTIE=0'))
        os.remove(os.path.join(tmp, 'code-installateur.txt'))
        cas('plantage : le code passe à AMP (relance par AMP)', {}, 3, codes=(3,), dehors=('mise a jour demandee', 'INSTALLATEUR'))
        cas('code 75 sans adresse des versions', {'AdresseVersions': ''}, 0, codes=(75, 0), dedans=('est vide : pas d\'Update',), dehors=('INSTALLATEUR',))
        os.remove(os.path.join(tmp, 'installer-taverne.sh'))
        cas('réglages changés', {'PenduleDefaut': '0', 'SignesBots': 'non', 'DerriereNginx': 'non', 'UrlPublique': 'https://jeux.exemple.fr/',
                                  'Origines': 'https://jeux.exemple.fr, https://exemple.fr', 'GarderParties': '7', '$MaxUsers': '50'}, 0,
            dedans=('TAVERNE_PENDULE_MS=0', 'TAVERNE_SIGNES_BOTS=0', 'TAVERNE_PROXY=0', 'TAVERNE_URL_PUBLIQUE=https://jeux.exemple.fr\n',
                    'TAVERNE_ORIGINES=https://jeux.exemple.fr,https://exemple.fr', 'TAVERNE_GARDER_PARTIES_JOURS=7', 'TAVERNE_MAX_CONNEXIONS=50'))
        cas('clé vide', {}, 1, cle='', dedans=('ERREUR : la cle des jetons (TAVERNE_CLE_JETON) est vide', 'openssl rand -hex 32',
                                                  'Reglages, Site, La Taverne'), dehors=('ARGS=',))
        cas('pas de clé du tout', {}, 1, cle=None, dedans=('est vide',), dehors=('ARGS=',))
        cas('clé courte', {}, 1, cle='abc123', dedans=('trop courte : 6 caracteres',), dehors=('ARGS=',))
        cas('clé d\'exemple', {}, 1, cle='remplacer-par-64-caracteres-hexadecimaux-tires-au-hasard-0000000', dedans=('celle de l\'exemple',))
        cas('clé avec espace', {}, 1, cle='0123456789abcdef0123456789abcdef 0123', dedans=('caractere special',))
        cas('la clé arrive au jeu par l\'environnement', {}, 0, cle='f' * 64, dedans=('TAVERNE_CLE_JETON=' + 'f' * 64,))  # seul le bouchon l'écrit
        cas('argument glissé par un guillemet', {'UrlPublique': 'https://a.fr" "url=https://b.fr'}, 1, dedans=('en double',))
        cas('adresse avec chemin', {'UrlPublique': 'https://taverne.teamkit.fr/jeu'}, 1, dedans=('adresse publique',))
        cas('origine fausse', {'Origines': 'https://taverne.teamkit.fr,javascript:alert(1)'}, 1, dedans=('origine \'javascript:alert(1)\'',))
        cas('origines vides', {'Origines': ''}, 1, dedans=('origines autorisees vides',))
        cas('port faux', {'$HttpPort': '80'}, 1, dedans=('port : \'80\'',))
        cas('borne', {'MaxTables': '999999'}, 1, dedans=('tables au plus',))
        cas('pendule inconnue', {'PenduleDefaut': '45000'}, 1, dedans=('temps par coup',))
        cas('guillemet', {'UrlPublique': 'https://a.fr" "x=1'}, 1, dedans=('ERREUR',))
        os.remove(os.path.join(tmp, 'jeu', 'VERSION'))
        cas('jeu absent', {}, 1, dedans=('La Taverne n\'est pas installee', 'Update'))
        # la ligne de la clé ne sort jamais du lanceur lui-même
        if 'CLE' in re.sub(r'#.*', '', lanceur).split('echo "TeamKit')[1].split('exec')[0]:
            soucis.append('le lanceur ne doit jamais écrire la clé')
        if not [s for s in soucis if s.startswith('lanceur')]:
            ok('aller-retour lanceur', '%d cas par le vrai lanceur (bash) : réglages, refus, console, relance après « maintenance »' % (n + 1))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# ── 8. le vrai installateur contre un faux dossier de versions ──────────────
JETON = 'jeton-essai-' + 'ab12' * 4


def fausse_version(dossier, version, alterer=False):
    """Une version miniature au format de outils/fabriquer-version.mjs (dossier taverne-<v>/)."""
    fichiers = {
        'VERSION': version + '\n',
        'serveur/src/index.js': 'console.log("taverne %s");\n' % version,
        'serveur/node_modules/ws/package.json': '{"name":"ws","version":"8.21.3"}\n',
        'moteur/src/index.js': 'export const v = "%s";\n' % version,
        'client/index.html': '<!doctype html><title>La Taverne %s</title>\n' % version,
    }
    emp = ''.join('%s  %s\n' % (hashlib.sha256(c.encode()).hexdigest(), p) for p, c in sorted(fichiers.items()))
    fichiers['EMPREINTES.sha256'] = emp
    if alterer:
        fichiers['client/index.html'] += '<script>pirate()</script>\n'
    nom = 'taverne-%s.tar.gz' % version
    with tarfile.open(os.path.join(dossier, nom), 'w:gz') as t:
        for p, c in sorted(fichiers.items()):
            b = c.encode()
            i = tarfile.TarInfo('taverne-%s/%s' % (version, p))
            i.size = len(b)
            i.mtime = 1760000000
            t.addfile(i, io.BytesIO(b))
    sha = hashlib.sha256(open(os.path.join(dossier, nom), 'rb').read()).hexdigest()
    with open(os.path.join(dossier, nom + '.sha256'), 'w', newline='\n') as f:
        f.write('%s  %s\n' % (sha, nom))
    return sha


if bash:
    racine = tempfile.mkdtemp(prefix='taverne-versions-')
    inst = tempfile.mkdtemp(prefix='taverne-instance-')
    try:
        os.makedirs(os.path.join(racine, 'versions'))
        vdir = os.path.join(racine, 'versions')
        fausse_version(vdir, '0.1.0')
        with open(os.path.join(vdir, 'DERNIERE'), 'w', newline='\n') as f:
            f.write('0.1.0\n')
        vus = []

        class Gardien(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=racine, **k)

            def do_GET(self):
                vus.append(self.headers.get('Authorization', ''))
                if self.headers.get('Authorization') != 'Bearer ' + JETON:
                    self.send_error(401)
                    return
                super().do_GET()

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Gardien)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        source = 'http://127.0.0.1:%d/versions' % srv.server_address[1]
        # Node déjà « installé » (bouchon à la bonne version) : l'essai ne télécharge rien d'Internet.
        os.makedirs(os.path.join(inst, 'node', 'bin'))
        with open(os.path.join(inst, 'node', 'bin', 'node'), 'w', newline='\n') as f:
            f.write('#!/bin/bash\n[ "$1" = "--version" ] && echo v22.23.3\nexit 0\n')
        os.makedirs(os.path.join(inst, 'donnees'))
        with open(os.path.join(inst, 'donnees', 'stats.json'), 'w') as f:
            f.write('{"kevin":{"belote":1500}}')
        shutil.copy(os.path.join(ICI, INSTALLEUR), inst)
        m8 = 0

        def installer(version='', jeton=JETON, source_=None):
            args = rendre(args_maj, {'$FullBaseDir': unix(inst) + '/', 'AdresseVersions': source_ or source, 'JetonVersions': jeton, 'VersionVoulue': version})
            p = subprocess.run([bash] + args, capture_output=True, timeout=120)
            return p.returncode, (p.stdout + p.stderr).decode('utf-8', 'replace')

        def essai(titre, attendu_code, dedans=(), **k):
            global m8
            m8 += 1
            c, sortie = installer(**k)
            if c != attendu_code or any(d not in sortie for d in dedans):
                soucis.append('installateur, %s : code %d\n%s' % (titre, c, sortie))
            if JETON in sortie:
                soucis.append('installateur, %s : le jeton apparaît dans la sortie' % titre)
            return sortie

        def version_installee():
            try:
                return open(os.path.join(inst, 'jeu', 'VERSION')).read().strip()
            except OSError:
                return None

        essai('mauvais jeton', 1, ('jeton refuse',), jeton='faux-jeton')
        if version_installee() is not None:
            soucis.append('installateur : rien ne doit être installé avec un mauvais jeton')
        essai('première installation', 0, ('Empreinte SHA-256 verifiee', 'La Taverne 0.1.0 installee', 'Donnees gardees'))
        if version_installee() != '0.1.0':
            soucis.append('installateur : 0.1.0 devrait être installée')
        essai('déjà installée', 0, ('deja installee',))
        # empreinte publiée fausse : rien ne change
        fausse_version(vdir, '0.1.1')
        with open(os.path.join(vdir, 'taverne-0.1.1.tar.gz.sha256'), 'w') as f:
            f.write('0' * 64 + '  taverne-0.1.1.tar.gz\n')
        essai('empreinte fausse', 1, ('empreinte SHA-256 de taverne-0.1.1.tar.gz incorrecte',), version='0.1.1')
        # un fichier modifié dans l'archive (empreinte de l'archive juste, EMPREINTES.sha256 non)
        fausse_version(vdir, '0.1.1', alterer=True)
        essai('fichier modifié', 1, ('ne correspond pas a EMPREINTES.sha256',), version='0.1.1')
        if version_installee() != '0.1.0':
            soucis.append('installateur : une archive refusée ne doit rien changer')
        # vraie mise à jour
        fausse_version(vdir, '0.1.1')
        with open(os.path.join(vdir, 'DERNIERE'), 'w', newline='\n') as f:
            f.write('0.1.1\n')
        essai('mise à jour', 0, ('La Taverne 0.1.1 installee (a la place de 0.1.0', 'Copie de surete des donnees'))
        if version_installee() != '0.1.1' or open(os.path.join(inst, 'jeu.ancien', 'VERSION')).read().strip() != '0.1.0':
            soucis.append('installateur : 0.1.1 installée, 0.1.0 gardée dans jeu.ancien/')
        if not glob.glob(os.path.join(inst, 'sauvegardes', 'donnees-avant-0.1.1-*.tar.gz')):
            soucis.append('installateur : copie des données avant la mise à jour absente')
        essai('retour à 0.1.0', 0, ('La Taverne 0.1.0 installee (a la place de 0.1.1',), version='0.1.0')
        essai('version introuvable', 1, ('introuvable',), version='9.9.9')
        essai('version mal écrite', 1, ('version voulue',), version='latest')
        essai('adresse en clair refusée', 1, ('Adresse des versions',), source_='http://exemple.fr/versions')
        essai('dépôt GitHub sans jeton', 1, ('colle un jeton de lecture',), jeton='', source_='github:hydrocut/taverne')
        if open(os.path.join(inst, 'donnees', 'stats.json')).read() != '{"kevin":{"belote":1500}}':
            soucis.append('installateur : donnees/ a été touché')
        if [a for a in vus if a and a != 'Bearer ' + JETON and a != 'Bearer faux-jeton']:
            soucis.append('installateur : en-tête Authorization inattendu')
        if os.listdir(os.path.join(inst, '.cache')):
            soucis.append('installateur : .cache/ pas vidé (%s)' % os.listdir(os.path.join(inst, '.cache')))
        srv.shutdown()
        if not [s for s in soucis if s.startswith('installateur')]:
            ok('installateur', '%d cas par le vrai installateur (bash, faux dossier de versions)' % m8)
    finally:
        shutil.rmtree(racine, ignore_errors=True)
        shutil.rmtree(inst, ignore_errors=True)

# ── 9. regex de console ──────────────────────────────────────────────────────
# Lignes réelles du serveur (essais du 13/10/2026 : conteneur ampbase:debian et PC, voir FAISABILITE.md).
ESSAIS = [
    ('Console.AppReadyRegex', '[taverne] Prêt : La Taverne 0.1.0 écoute sur le port 8790 (http://127.0.0.1:8796).', {}),
    ('Console.AppReadyRegex', '[taverne] Prêt : La Taverne 0.1.0 écoute sur le port 8790 (https://taverne.teamkit.fr).', {}),
    ('Console.UserJoinRegex', '[taverne] Arrivée : Invité-8725 [invité]', {'username': 'Invité-8725'}),
    ('Console.UserJoinRegex', '[taverne] Arrivée : Essai Prod [compte]', {'username': 'Essai Prod'}),
    ('Console.UserLeaveRegex', '[taverne] Départ : Invité-8725', {'username': 'Invité-8725'}),
    ('Console.UserJoinRegex', '[taverne] Arrivée : Kevin [staff]', {'username': 'Kevin'}),
    ('Console.UserJoinRegex', '[taverne] Arrivée : Marine [compte]', {'username': 'Marine'}),
    ('Console.UserJoinRegex', '[taverne] Arrivée : Le [roi] du pli [compte]', {'username': 'Le [roi] du pli'}),
    ('Console.UserLeaveRegex', '[taverne] Départ : Invité 4821', {'username': 'Invité 4821'}),
    ('Console.UserLeaveRegex', '[taverne] Départ : Le [roi] du pli', {'username': 'Le [roi] du pli'}),
    # Mise à jour en douceur (serveur/test/maintenance-sortie.test.js) : chacun « part » à l'arrêt (la liste des joueurs
    # d'AMP se vide), puis la ligne « Prêt » revient après la relance faite par le lanceur.
    ('Console.UserLeaveRegex', '[taverne] Départ : Kevin', {'username': 'Kevin'}),
    ('Console.AppReadyRegex', '[taverne] Prêt : La Taverne 0.2.0 écoute sur le port 8790 (https://taverne.teamkit.fr).', {}),
]
REFUS = [
    ('Console.AppReadyRegex', '[taverne] production, jeux : belote, coinche, echecs, dames'),
    ('Console.AppReadyRegex', 'TeamKit : La Taverne 0.1.0 avec Node.js v22.23.3, port 8790, adresse https://taverne.teamkit.fr'),
    ('Console.UserJoinRegex', '[taverne] Départ : Kevin'),
    ('Console.UserJoinRegex', '[taverne] En ce moment : 2 joueur(s) connecté(s), 1 table(s) en cours.'),
    ('Console.UserLeaveRegex', '[taverne] Arrivée : Kevin [compte]'),
    ('Console.UserLeaveRegex', '[taverne] SIGTERM reçu : arrêt propre…'),
    ('Console.UserLeaveRegex', '[taverne] arrêt propre : 1 table(s) sauvegardée(s).'),
    ('Console.AppReadyRegex', '[taverne] 1 table(s) reprise(s) après redémarrage.'),
    ('Console.AppReadyRegex', '[taverne] production, jeux : belote, coinche, echecs, dames, poker, poker-tournoi, poker-duel, client servi ici (145 fichiers, empreinte b774e51c073d)'),
    # Lignes de la mise à jour en douceur : ni arrivée, ni départ, ni « prêt ».
    ('Console.UserJoinRegex', '[taverne] Maintenance : mise à jour annoncée dans 10 min (console). Plus de nouvelle partie ; les parties en cours continuent.'),
    ('Console.UserLeaveRegex', '[taverne] Maintenance : mise à jour annoncée dans 10 min (console). Plus de nouvelle partie ; les parties en cours continuent.'),
    ('Console.AppReadyRegex', '[taverne] Maintenance : arrêt dans 1 min.'),
    ('Console.UserLeaveRegex', '[taverne] Maintenance : c\'est l\'heure : sauvegarde des parties en cours, arrêt, puis Update et relance (code de sortie 75).'),
    ('Console.UserLeaveRegex', '[taverne] arrêt propre pour la mise à jour : 1 table(s) sauvegardée(s).'),
    ('Console.UserLeaveRegex', '[taverne] Sortie avec le code 75 : relance demandée (mise à jour).'),
    ('Console.AppReadyRegex', 'TeamKit : mise a jour demandee par le jeu (code 75) : Update de La Taverne, puis relance...'),
    ('Console.AppReadyRegex', 'TeamKit : La Taverne 0.2.0 installee (a la place de 0.1.0, gardee dans jeu.ancien/). Donnees gardees : donnees/ (2,1M).'),
    ('Console.UserLeaveRegex', '[taverne] Partie annulée après la mise à jour (belote, table T1a2b3c4d5, 2 joueur(s) prévenu(s)) : règles changées : règles du jeu changées (version 1 → 2).'),
    ('Console.UserJoinRegex', '[taverne] Maintenance : dernier démarrage : 3 partie(s) reprise(s), 1 annulée(s) (belote : règles changées).'),
    ('Console.UserJoinRegex', '[taverne] Maintenance : « 7 » : écris maintenance 5, 10, 15, maintenant, annuler, etat ou a 05:00 10. Tape « aide ».'),
]
for cle, l, groupes in ESSAIS:
    mm = py(cles[cle]).search(l)
    if not mm or any(mm.group(k) != v for k, v in groupes.items()):
        soucis.append('%s devrait reconnaître %r' % (cle, l))
for cle, l in REFUS:
    if py(cles[cle]).search(l):
        soucis.append('%s ne devrait pas reconnaître %r' % (cle, l))
# Les mêmes lignes, telles que le serveur les écrit (src/index.js, src/taverne.js du dépôt privé), si on l'a sous la main.
serveur = os.path.join(ICI, '..', 'Taverne', 'serveur', 'src')
if os.path.isdir(serveur):
    src = ''.join(io.open(os.path.join(serveur, f), encoding='utf-8').read() for f in ('taverne.js', 'index.js', 'maintenance.js'))
    for morceau in ('[taverne] Arrivée : ${s.pseudo} [${', '[taverne] Départ : ${s.pseudo}', '[taverne] Prêt : La Taverne ${',
                    "'[taverne] Maintenance : '", 'process.exit(o.code || 0)', 'CODE_RELANCE = 75'):
        if morceau not in src:
            soucis.append('le serveur n\'écrit plus « %s » : regex à revoir' % morceau)
ok('console', '%d lignes reconnues, %d refusées' % (len(ESSAIS), len(REFUS)))

# ── 10. aucun secret ─────────────────────────────────────────────────────────
for p in glob.glob(os.path.join(ICI, '**', '*'), recursive=True):
    if os.path.isdir(p) or '.git' + os.sep in p or p.endswith(('.pyc', 'FAISABILITE.md')):
        continue
    t = io.open(p, encoding='utf-8', errors='replace').read()
    for motif, quoi in ((r'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}', 'jeton GitHub'),
                        (r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])', 'clé de 64 caractères hexadécimaux')):
        for x in re.findall(motif, t):
            if x == re.search(r'^NODE_SHA=([0-9a-f]{64})$', installeur, re.M).group(1):
                continue   # empreinte publique de Node.js
            soucis.append('%s : %s dans le dépôt' % (os.path.relpath(p, ICI), quoi))
ok('secrets', 'aucun jeton ni clé (hors empreinte publique de Node.js)')

print()
if soucis:
    print('%d problème(s) :' % len(soucis))
    print('\n'.join('  NON  ' + s for s in soucis))
    sys.exit(1)
print('Tout est bon.')
