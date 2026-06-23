# LMS AI

Plateforme LMS Flask autonome pour gerer des classes, cours, planning, devoirs, quiz, ressources, annonces, questions/reponses, certificats et comptes etudiants/professeurs.

## Lancement

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python app.py
```

Puis ouvrir `http://127.0.0.1:5001/lms/login`.

## Acces de demonstration

- Professeur : `prof@iua.ci` / `prof123`
- Etudiant : `archille.kouame@iua.ci` / `demo123`

## Fonctionnalites

- Back-office professeur
- Gestion des classes et changement de classe active
- Invitation d'etudiants avec lien d'activation
- Activation/desactivation des comptes etudiants
- Reinitialisation de mot de passe par lien
- Boite d'envoi interne pour emails generes
- Interface etudiant
- Traduction FR/EN
- Base SQLite locale
