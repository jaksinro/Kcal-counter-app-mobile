# CalTrack (version web)

Suivi des calories et des macros, version outil web de l'appli mobile CalTrack.
On note ce qu'on mange au gramme près ; l'outil calcule calories, protéines, glucides,
lipides, sucres, fibres, sel et compare aux objectifs du jour.

## Lancer
Double-cliquer sur `lancer.bat` (ou `python server.py`, port 8001 par défaut ; `python server.py 8080` pour un autre port).
La console affiche l'adresse à ouvrir sur les téléphones, ex. `http://192.168.1.186:8001`.
(Port 8001 pour pouvoir lancer en même temps l'outil « Courses », qui utilise le 8000.)

## Lancement automatique au démarrage de Windows
Un raccourci « CalTrack (serveur) » est placé dans le dossier Démarrage de Windows
(touches Windows + R, puis `shell:startup`). Il lance le serveur **sans fenêtre** à chaque ouverture de session :
l'appli est directement disponible sur http://localhost:8001.
- Après une mise à jour de `server.py` : double-cliquer sur `redemarrer.bat`.
- Pour ne plus le lancer au démarrage : supprimer le raccourci du dossier Démarrage.
- `lancer.bat` n'est plus nécessaire : le serveur tourne déjà (il afficherait « port déjà utilisé »).

## Accès depuis le wifi
Windows bloque par défaut les connexions entrantes. Une seule fois, dans PowerShell **en administrateur** :

    New-NetFirewallRule -DisplayName "CalTrack (port 8001)" -Direction Inbound -Protocol TCP -LocalPort 8001 -Action Allow -Profile Private

(le réseau wifi doit être en profil « Privé » dans Paramètres > Réseau).

## Profils
À la première utilisation, l'appli demande de **créer un profil** (un prénom suffit).
Ensuite, à chaque ouverture, elle demande qui l'utilise ; le bouton **+ Nouveau profil** en ajoute un autre.
Chaque profil a son propre journal, ses objectifs et ses aliments perso (la base d'aliments est commune).
Pour changer de profil en cours de route : toucher le nom en haut à droite.
Les profils sont listés dans `profiles.json` et leurs données dans `data-<identifiant>.json`
(ces fichiers restent sur le PC : ils ne sont pas envoyés sur GitHub).

## Fonctionnement
- **Journal** : anneau des calories, barres protéines / glucides / lipides, aliments rangés par repas.
  Flèches ‹ › (ou toucher la date) pour voir un autre jour. Toucher un aliment pour changer la quantité,
  le repas, ou le supprimer.
- **Ajouter** : choisir le jour et le repas, puis
  - **Base** : ~400 aliments courants (Ciqual / USDA), recherche sans accents, filtres par catégorie,
    et les aliments récents quand la recherche est vide ;
  - **En ligne** : recherche Open Food Facts (produits du commerce) ou par code-barres.
    « ☆ Garder » enregistre le produit dans vos aliments ;
  - **Photo** : prendre son assiette en photo ; un modèle d'IA installé sur le PC reconnaît les aliments
    et estime les quantités (voir plus bas) ;
  - **Créer** : saisir un aliment à partir de son étiquette (valeurs pour 100 g).
- **Historique** : graphique des calories sur 7 ou 30 jours, moyennes, liste des jours (toucher un jour pour l'ouvrir).
- **Objectifs** : calories et macros du jour, liste de vos aliments perso, copie de sauvegarde.

Toutes les données sont dans les fichiers `data-<prénom>.json` (à sauvegarder si besoin). La base d'aliments est `foods.json`.
La recherche en ligne passe par le PC : il doit avoir accès à internet.

## Photo du repas (IA locale)
Onglet **Ajouter > 📷 Photo** : prendre l'assiette en photo (bien éclairée, plutôt vue de dessus),
ajouter si besoin une précision (« riz complet », « sauce au curry »), puis **Analyser**.
La liste des aliments reconnus s'affiche avec des grammages estimés : corriger les quantités,
décocher ce qui est faux, puis **Ajouter au journal**.
- Les aliments trouvés dans la base prennent ses valeurs ; les autres sont marqués **ESTIMÉ**
  (valeurs données par le modèle).
- Les quantités sont des estimations (souvent ± 20 à 40 %) : à vérifier.
- Durée : ~25 s pour la première photo (chargement du modèle), puis ~10 à 20 s.

Fonctionnement : tout reste sur le PC, rien ne part sur internet.
- Modèle : **Qwen3.5-9B** (Q6_K), fichiers dans `%USERPROFILE%\.lmstudio\models\lmstudio-community\Qwen3.5-9B-GGUF\`
  (ne pas le supprimer depuis LM Studio).
- Moteur : **llama.cpp** (version Windows CUDA 12), dans `%LOCALAPPDATA%\llama.cpp\`.
- CalTrack lance le moteur **à la première photo** (sans fenêtre) et l'**arrête après 10 minutes** sans photo :
  la carte graphique (~7,5 Go) est libérée pour le reste du temps. Réglage : `VISION_IDLE` dans `server.py`.

## Sur téléphone
Ouvrir l'adresse dans le navigateur puis l'ajouter à l'écran d'accueil pour l'avoir comme une appli :
- iPhone (Safari) : bouton Partager > « Sur l'écran d'accueil »
- Android (Chrome) : menu ⋮ > « Ajouter à l'écran d'accueil »

Les appareils se synchronisent tout seuls (toutes les 5 s).

## Accès à distance (hors de la maison) — Tailscale
Même principe que l'outil Courses :
1. Installer Tailscale sur le PC et les téléphones, avec le même compte.
2. Sur le PC : `tailscale serve --bg --https=8443 8001`
   -> adresse du type `https://nom-du-pc.xxxx.ts.net:8443` à ouvrir sur les téléphones.
   (Si l'outil Courses n'utilise pas déjà `tailscale serve`, `tailscale serve --bg 8001` suffit.)
En HTTPS, sur Chrome Android, un bouton 📷 permet aussi de scanner les codes-barres avec la caméra.
