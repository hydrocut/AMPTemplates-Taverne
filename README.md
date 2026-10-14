# AMPTemplates-Taverne — La Taverne (TeamKit's 3D game lounge) for AMP

**AMP Generic Module** template for **La Taverne**, TeamKit's 3D card and board game lounge played in the
web browser (belote, coinche, chess, draughts, play-money poker), against bots or friends.
Written for TeamKit, a free French game host, in October 2026.

**The game itself is private.** This public repository holds only the AMP template: AMP clones template
repositories without credentials, so the template must be public, but it contains **no game code**. At each
Update, the template downloads a ready-made release (`taverne-<version>.tar.gz`) from a configurable address
with a **read-only token**, checks its SHA-256, and installs it next to the data, which it never touches.

> Résumé en français à la fin du fichier.

## What it does

| | |
|---|---|
| Server | one Node.js process serves the game (WebSocket `/ws`) **and** its web page on **one TCP port** (default 8790) |
| Runtime | Node.js **22.23.3**, official `nodejs.org` binary, version and SHA-256 pinned in `scripts/installer-taverne.sh` (Debian 13's package is Node 20, too old) |
| Image | `cubecoders/ampbase:debian`, **container required** (uses `curl`, `jq`, `tar`, `xz`, `sha256sum` of the image) |
| Game code | private release, downloaded with a read-only token (GitHub release of a private repo, or a protected web folder) |
| Data | `donnees/` (stats, profiles, saved tables, moderation): never touched by an Update; copied to `sauvegardes/` before each version change (last 5 kept) |
| Players in AMP | listed: the server prints `[taverne] Arrivée : <name> [compte\|invité\|staff]` and `[taverne] Départ : <name>` |
| Stop | `SIGTERM`: the server tells players, saves every table in progress to `donnees/`, and exits (15 s max); tables resume at the next start |
| Graceful update | type `maintenance 10` in AMP's console (or let AMP's scheduler send it): 10-minute countdown banner for every player, no new game, then save, **Update and restart by the start script itself** (exit code 75) |
| HTTPS | done in front by the host's nginx (`taverne.teamkit.fr`); the game port must stay **closed to the world** |

## Files

| File | Role |
|---|---|
| `taverne.kvp`, `taverne*.json`, `manifest.json` | the AMP template |
| `scripts/installer-taverne.sh` | Update step: Node.js 22 + the private release (fetched from this repository at each Update) |
| `scripts/lancer-taverne.sh` | start script: checks every setting, refuses to start with a clear message, runs Node (AMP's console goes to its input, AMP's SIGTERM is passed on), and on exit code 75 runs the installer then starts the game again |
| `verifier.py` | pre-push checks (runs both scripts for real under bash), must print `Tout est bon.` |

## Instance folder

```
taverne/
  node/            Node.js 22 (pinned, SHA-256 checked)
  jeu/             installed release: serveur/ (with its production node_modules), moteur/, client/, VERSION
  jeu.ancien/      the previous release
  donnees/         game data — NEVER touched by an Update
  sauvegardes/     copy of donnees/ before each version change
  installer-taverne.sh, lancer-taverne.sh
```

## Where releases come from (setting « Adresse des versions »)

1. **`github:hydrocut/taverne`** (recommended): releases of the private repository. The token is a GitHub
   **fine-grained personal access token** restricted to that **one repository**, permission **Contents: read-only**,
   nothing else. The script calls `GET /repos/<repo>/releases/latest` (or `/releases/tags/v<version>`), downloads the
   assets `taverne-<version>.tar.gz` and `taverne-<version>.tar.gz.sha256` through the API
   (`Accept: application/octet-stream`), and also compares GitHub's own `digest` of the asset when present.
2. **`https://<host>/<folder>`**: a web folder holding `DERNIERE` (latest version number), `taverne-<version>.tar.gz`
   and its `.sha256`, protected by nginx with `Authorization: Bearer <token>`.

The token is never printed and never on curl's command line (header file readable by the instance only, deleted on
exit); curl drops it when GitHub redirects to its storage. Then: archive SHA-256 → extraction next to the current
release → `VERSION` and every file checked against `EMPREINTES.sha256` → `node --check` → copy of `donnees/` → swap.
A refused archive changes nothing. **Rollback:** set « Version voulue » to the previous number, Update, Start.

## Settings exposed in AMP (French UI)

| Setting | Variable | Default |
|---|---|---|
| Clé des jetons (password, required, ≥ 32 chars) | `TAVERNE_CLE_JETON` (environment, never on a command line) | empty → refuses to start |
| Adresse publique | `TAVERNE_URL_PUBLIQUE` | `https://taverne.teamkit.fr` |
| Origines autorisées | `TAVERNE_ORIGINES` | `https://taverne.teamkit.fr,https://teamkit.fr,https://www.teamkit.fr` |
| Derrière nginx | `TAVERNE_PROXY` | oui |
| Adresse des versions / Jeton de lecture (password) / Version voulue | Update, and the start script (restart after `maintenance`; the token goes through `TAVERNE_JETON_VERSIONS`, removed before Node starts) | `github:hydrocut/taverne` / empty / latest |
| Délai d'annonce par défaut | `TAVERNE_MAINTENANCE_MIN` (`maintenance` typed alone) | 10 min (5, 10 or 15) |
| Temps par coup (par défaut) | `TAVERNE_PENDULE_MS` | 30 s |
| Signes des bots | `TAVERNE_SIGNES_BOTS` | oui |
| Parties / statistiques / modération gardées | `TAVERNE_GARDER_*_JOURS` | 30 / 365 / 365 days |
| Connexions au plus (AMP's Max Users) / par adresse IP / tables au plus | `TAVERNE_MAX_*` | 1000 / 20 / 500 |
| Vocal intégré : adresse | `TAVERNE_VOIX_URL` | empty → no built-in voice chat (no button, nothing breaks) |
| Vocal intégré : clé (API key) / secret (passwords) | `TAVERNE_VOIX_CLE` / `TAVERNE_VOIX_SECRET` (environment; the start script hands them to the game only when the three voice settings are valid, otherwise it prints a warning and the game starts **without** voice) | empty |
| Salon Discord de la table | `TAVERNE_SALON_DISCORD` | non |
| Adresse du site TeamKit | `TAVERNE_SITE_URL` (signed route `/api/taverne/vocal`, for the Discord channel of a table) | `https://www.teamkit.fr` |

The voice server itself is a separate instance (`hydrocut/AMPTemplates-TaverneVoix`, LiveKit): its API key and secret are
the ones to paste here, the same on both instances.

## Graceful update (console command `maintenance`)

The console is writeable (`App.HasWriteableConsole=True`, `App.AdminMethod=STDIO`): what is typed in AMP's console
reaches the game.

| Command | Effect |
|---|---|
| `maintenance 10` (5, 10 or 15) | every connected player sees « 🔧 Mise à jour de la Taverne dans 9:59. Ta partie sera sauvegardée. »; no new game can start; games in progress go on (a card table does not start a deal it cannot finish) |
| `maintenance` | same, with the default delay (setting « Délai d'annonce par défaut ») |
| `maintenance maintenant` | emergency: save and stop now |
| `maintenance annuler` | cancels the countdown (and a scheduled one) |
| `maintenance etat` | countdown, schedule, and how many games resumed or were cancelled at the last start |
| `maintenance a 05:00 10` | the game itself starts the 10-minute countdown at 05:00, Paris time (once) |

At zero, the server tells players, saves the tables, prints one `Départ` line per player (AMP's user list empties)
and **exits with code 75**. The start script then runs `installer-taverne.sh` with the same three Update settings
(a newer release is installed if there is one; nothing happens otherwise) and starts the game again: the instance
never stops, players reconnect by themselves (« ✅ La Taverne est de retour ! »). A failed Update changes nothing and
the current version starts again. Any other exit code (crash) is handed to AMP, which restarts the game
(`Limits.AutoRetryCount=2`).

**Scheduled update with AMP's scheduler**: Schedule → new trigger (time interval, e.g. every day at 05:00, or one day)
→ task « Application - Send a command to rcon/console » with `maintenance 10`. Nothing else is needed: the restart and
the Update are done by the start script. Do **not** use « Update the application » or « Restart the application » for
this: they stop the game without the countdown (tables are still saved, but players get no warning).

## Behind nginx in HTTPS

The ready-to-use vhost is in the private game repository (`deploiement/taverne.teamkit.fr.conf`): `/ws` with
WebSocket upgrade, `X-Forwarded-For $remote_addr` (one address only), `limit_req` / `limit_conn`, noindex snippet,
HSTS; then `certbot --nginx -d taverne.teamkit.fr`. The game itself sends its cache headers (versioned scripts kept
one year), br/gzip compression and a strict Content-Security-Policy. Close port 8790 to the world with **two**
iptables rules (`INPUT ! -i lo` and `DOCKER-USER ! -i docker0`): a port published by Docker is DNATed and does not
go through INPUT.

## Console lines (real, 13 Oct 2026)

From the test in a throwaway `cubecoders/ampbase:debian` container and on a PC (see the French notes):

```
TeamKit : La Taverne 0.1.0 avec Node.js v22.23.3, port 8790, adresse http://127.0.0.1:8796
[taverne] production, jeux : belote, coinche, echecs, dames, poker, poker-tournoi, poker-duel, client servi ici (145 fichiers, empreinte b774e51c073d)
[taverne] Prêt : La Taverne 0.1.0 écoute sur le port 8790 (http://127.0.0.1:8796).   ← Console.AppReadyRegex
[taverne] Arrivée : Invité-8725 [invité]                                                ← Console.UserJoinRegex
[taverne] En ce moment : 1 joueur(s) connecté(s), 1 table(s) en cours.
[taverne] Arrivée : Essai Prod [compte]
[taverne] Départ : Essai Prod                                                           ← Console.UserLeaveRegex
[taverne] SIGTERM reçu : arrêt propre…
[taverne] arrêt propre : 1 table(s) sauvegardée(s).
[taverne] 1 table(s) reprise(s) après redémarrage.
```

Graceful update (`serveur/test/maintenance-sortie.test.js` and this repository's `verifier.py`):

```
maintenance 10                                                   ← typed in AMP's console
[taverne] Maintenance : mise à jour annoncée dans 10 min (console). Plus de nouvelle partie ; les parties en cours continuent, …
[taverne] Maintenance : arrêt dans 5 min.
[taverne] Maintenance : arrêt dans 1 min.
[taverne] Maintenance : c'est l'heure : sauvegarde des parties en cours, arrêt, puis Update et relance (code de sortie 75).
[taverne] Départ : Kevin                                          ← Console.UserLeaveRegex (AMP's user list empties)
[taverne] arrêt propre pour la mise à jour : 1 table(s) sauvegardée(s).
[taverne] Sortie avec le code 75 : relance demandée (mise à jour).
TeamKit : mise a jour demandee par le jeu (code 75) : Update de La Taverne, puis relance...
TeamKit : La Taverne 0.2.0 installee (a la place de 0.1.0, gardee dans jeu.ancien/). Donnees gardees : donnees/ (2,1M).
[taverne] Prêt : La Taverne 0.2.0 écoute sur le port 8790 (https://taverne.teamkit.fr).
[taverne] 1 table(s) reprise(s) après redémarrage.
```

When a setting is wrong, the start script refuses with a message, for example:

```
ERREUR : la cle des jetons (TAVERNE_CLE_JETON) est vide : La Taverne refuse de demarrer.
  1. Fabrique une cle (sur le dedie ou dans un terminal) : openssl rand -hex 32
  2. Colle-la dans AMP : onglet Configuration, rubrique La Taverne, champ 'Cle des jetons'.
  3. Colle LA MEME dans le panel TeamKit : Reglages, Site, La Taverne.
  4. Relance l'instance.
```

## Before pushing

`python verifier.py` must print `Tout est bon.` It runs the real start script (25 cases, Node replaced by a stub:
settings, refusals, console passed to the game, read token kept away from the game, restart with Update after exit
code 75, failed Update, crash code handed to AMP) and the real installer against a local fake release folder protected by a token (11 cases: install, already
installed, wrong token, wrong SHA-256, tampered file, update, rollback, data untouched…).

## Install in AMP

1. ADS → Configuration → Instance Deployment → **Configuration Repositories**: add `hydrocut/AMPTemplates-Taverne:main`, save, **Fetch latest**.
2. Create an instance « La Taverne (TeamKit) » (container), set the two passwords (token key, read token).
3. **Update**, then **Start**. The console shows `Prêt` when ready.

A « Fetch latest » does **not** update an existing instance's `App.*`/`Console.*` keys nor its settings form
(TeamKit note of 30 Sept 2026): after a template change, recreate the instance or copy the keys by hand.

## Résumé en français

Template AMP de **La Taverne**, le salon de jeux 3D de TeamKit. Le **code du jeu est privé** : ce dépôt public ne
contient que le template. À chaque **Update**, il installe **Node.js 22** (binaire officiel, empreinte vérifiée), puis
télécharge la version du jeu avec un **jeton de lecture** (release du dépôt GitHub privé `hydrocut/taverne`, ou dossier
web protégé), vérifie son **SHA-256** et chaque fichier, garde l'ancienne version dans `jeu.ancien/` et **ne touche
jamais `donnees/`** (copie dans `sauvegardes/` avant chaque changement de version). Un seul port (8790) pour la page et
le WebSocket, derrière nginx en HTTPS, **fermé au monde** par le pare-feu. La **clé des jetons** (la même que dans le
panel TeamKit, Réglages → Site → 🍺 La Taverne) est obligatoire : vide ou trop courte, le jeu refuse de démarrer et
explique quoi faire dans la console. Arrêt propre par SIGTERM : les tables en cours sont sauvées et reprises.
Retour arrière : « Version voulue » = l'ancien numéro, Update, Start. Avant de pousser : `python verifier.py` → « Tout est bon. »

**Mise à jour en douceur** : taper `maintenance 10` dans la console d'AMP (5, 10 ou 15 min ; `maintenance` seul = le
« Délai d'annonce par défaut » ; `maintenance annuler`, `maintenance etat`, `maintenance a 05:00 10`). Les joueurs voient
le compte à rebours, plus aucune partie ne démarre, puis le jeu sauvegarde, sort avec le code 75, et le lanceur fait
l'Update et relance le jeu tout seul. Programmé : planificateur d'AMP, tâche « Envoyer une commande au RCON ou à la
console » avec `maintenance 10` (rien d'autre).
