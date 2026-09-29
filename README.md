# Kcal-counter-app-mobile/web
App to count kcal and macro in food. You give the weight of what you ate and with and API to some things that get the kcal and macro of nutriment you get the total. Get shredded kings &amp; queens
The app is pretty much working good already, it would be cool to create some DIY shit that can detect the aliment and weight it to automate the process. 

# This app will be free

# No adds

# Made with a lot of AI because I sucks at language to develop on mobile




## Version web (`web/`)

Outil web à lancer sur un PC Windows, utilisable depuis les téléphones de la maison (wifi) :
journal par repas, objectifs, historique, base de ~400 aliments, recherche Open Food Facts / code-barres,
plusieurs personnes, et **photo du repas analysée par une IA locale** (Qwen3.5-9B via llama.cpp, rien ne part sur internet).

Serveur Python sans dépendance : `python web/server.py` puis ouvrir http://localhost:8001.
Mode d'emploi complet : [`web/LISEZMOI.md`](web/LISEZMOI.md).
