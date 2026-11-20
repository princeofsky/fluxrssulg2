# Flux RSS avancé des actualités ULiège

Ce dépôt génère un flux RSS à partir de :
https://www.news.uliege.be/cms/c_9435330/fr/portail-news-agendas-toutes-les-news

Il fonctionne avec **GitHub Actions + GitHub Pages** : pas besoin de serveur, NAS ou hébergement payant.

## Installation

1. Crée un dépôt GitHub (public recommandé).
2. Décompresse l’archive et téléverse tout son contenu à la racine du dépôt, en conservant le dossier `.github`.
3. Dans **Settings → Pages**, sélectionne **GitHub Actions** comme source de publication.
4. Dans **Settings → Actions → General → Workflow permissions**, active **Read and write permissions**.
5. Dans **Actions**, lance **Update ULiège RSS feed** avec **Run workflow**.
6. Après une exécution réussie, le flux sera à `https://TON-PSEUDO.github.io/NOM-DU-DEPOT/feed.xml`.

## Fonctionnement

- Collecte planifiée toutes les 15 minutes (déclenchement manuel disponible).
- Parcourt jusqu’à 10 pages de 12 articles.
- Enrichit les nouveaux articles avec date, contenu HTML et image lorsque disponibles.
- Conserve l’historique dans `state/articles.json` et publie le flux RSS 2.0 dans `docs/feed.xml`.
- Ajoute `content:encoded` et `media:content` lorsque le site fournit ces éléments.

## Limites

GitHub Actions peut retarder les exécutions planifiées. Le collecteur dépend de la structure HTML du site (sélecteur actuel : `h3 a[href]`). Certains articles peuvent ne pas exposer de date. Le script n’a pas été exécuté dans ton compte GitHub : consulte l’onglet **Actions** après installation. Respecte les conditions d’utilisation du site et les droits liés aux contenus republiés.
