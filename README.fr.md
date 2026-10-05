# PDF Editor

**Remplir, signer, annoter et corriger des PDF sous Windows — gratuit, hors ligne, en français ou en anglais.**

*[English version](README.md)*

![PDF Editor affichant un rapport de plusieurs pages avec les miniatures et la barre d’outils](docs/screenshots/main-window.png)

[**Télécharger la dernière version**](https://github.com/cor3nt1nn/pdf-editor/releases/latest) ·
[Notes de version](https://github.com/cor3nt1nn/pdf-editor/releases) ·
[Signaler un problème](https://github.com/cor3nt1nn/pdf-editor/issues)

## Fonctionnalités

- **Remplir les formulaires PDF** — champs de texte, cases à cocher, boutons radio et listes
  déroulantes ; Tab passe au champ suivant.
- **Écrire sur les formulaires « plats »** — exports Word, formulaires imprimés ou numérisés :
  tapez du texte n’importe où et cochez avec les tampons ✓ ✗ ●. Texte et tampons
  s’alignent sur les cellules de tableau, les lignes et les cases, même sur un scan.
- **Signer avec une photo de votre signature** — photographiez-la ou numérisez-la une fois ;
  le fond du papier est retiré et la signature est conservée sur votre ordinateur, prête à
  être placée sur n’importe quelle page.
- **Surligner, souligner, barrer** et **copier le texte** de la page.
- **Modifier le texte de la page** — corriger une faute, un nom ou une date sur place, dans la
  police du document.
- **Outils de pages** — réordonner en faisant glisser les miniatures, supprimer, faire
  pivoter, insérer des pages blanches ou les pages d’un autre PDF, extraire, scinder.
- **Reconnaissance de texte (OCR)** — rendre les pages numérisées recherchables et
  sélectionnables, en français et en anglais, sans connexion Internet.
- **Exporter une copie** — aplatir champs, textes, tampons et signatures dans la page, sans
  les versions antérieures, les données cachées ni les propriétés du document.
- Tout peut être annulé (Ctrl+Z / Ctrl+Y). Les autres lecteurs PDF (Adobe Acrobat Reader,
  Edge, Chrome) affichent et impriment ce que vous ajoutez.
- Interface **en français ou en anglais**, claire ou sombre (suit Windows).

## Télécharger et installer

Windows 10 ou 11 (64 bits). Les deux téléchargements sont sur la
[dernière version publiée](https://github.com/cor3nt1nn/pdf-editor/releases/latest).

### Programme d’installation (recommandé)

1. Téléchargez `PDFEditor-<version>-setup.exe`.
2. Lancez-le. Le programme n’est pas signé : si Windows SmartScreen affiche « Windows a
   protégé votre ordinateur », cliquez sur **Informations complémentaires ▸ Exécuter quand
   même**.
3. Choisissez la langue, acceptez la licence et gardez **Installer pour moi uniquement**
   (aucun droit d’administrateur nécessaire). « Installer pour tous les utilisateurs »
   demande les droits d’administrateur.
4. Facultatif : un raccourci sur le bureau et **Ajouter PDF Editor à la liste « Ouvrir avec »
   des fichiers PDF**. L’installation ne change jamais votre programme PDF par défaut ; pour
   choisir PDF Editor, passez par **Paramètres ▸ Applications ▸ Applications par défaut** de
   Windows.
5. Lancez **PDF Editor** depuis le menu Démarrer.

**Mise à jour :** lancez l’installation de la nouvelle version. Elle propose de fermer PDF
Editor s’il est ouvert et garde vos paramètres et signatures.

**Désinstallation :** **Paramètres ▸ Applications ▸ Applications installées ▸ PDF Editor ▸
Désinstaller** dans Windows. Une question propose ensuite de supprimer aussi vos paramètres,
signatures et journaux (par défaut : les garder).

### Version portable (zip)

Sans installation — fonctionne depuis n’importe quel dossier, par exemple une clé USB.

1. Téléchargez `PDFEditor-<version>-win64.zip` et extrayez-le : vous obtenez un dossier
   `PDFEditor`.
2. Lancez `PDFEditor.exe` dans ce dossier (SmartScreen : **Informations complémentaires ▸
   Exécuter quand même**, la première fois seulement).
3. Facultatif : **Paramètres ▸ Enregistrer dans Windows (Ouvrir avec)…** ajoute PDF Editor à
   la liste « Ouvrir avec » des fichiers PDF. Si vous déplacez le dossier, recommencez depuis
   le nouvel emplacement.

Pour la retirer : **Paramètres ▸ Retirer de Windows** (si vous l’aviez enregistrée), puis
supprimez le dossier.

### Où sont vos données

Les deux versions partagent les mêmes données :

- paramètres : `%APPDATA%\PDFEditor\PDFEditor.ini`
- signatures enregistrées : `%LOCALAPPDATA%\PDFEditor\PDFEditor\signatures`
- journal : `%LOCALAPPDATA%\PDFEditor\PDFEditor\logs\pdfeditor.log` (indiqué aussi dans
  **Aide ▸ À propos de PDF Editor…**)

Pour tout effacer à la main, supprimez `%APPDATA%\PDFEditor` et `%LOCALAPPDATA%\PDFEditor`.

## Utilisation

Ouvrez un fichier avec **Fichier ▸ Ouvrir** (Ctrl+O), en le faisant glisser sur la fenêtre,
ou avec « Ouvrir avec » dans l’Explorateur. **Ctrl+S** enregistre ; **Aide ▸ Raccourcis
clavier…** (F1) liste tous les raccourcis.

### Remplir un formulaire

Un PDF à champs remplissables s’ouvre avec l’**outil formulaire** (F) actif et ses champs
teintés en bleu (**Affichage ▸ Surligner les champs de formulaire** retire la teinte).

- Cliquez dans un champ et tapez ; **Entrée** valide (Ctrl+Entrée dans un champ
  multiligne), **Échap** annule.
- Cliquez sur les cases et boutons radio (ou **Espace**) ; choisissez dans les listes.
- **Tab** / **Maj+Tab** passent au champ suivant / précédent, dans l’ordre de lecture.
- Une valeur trop longue est écrite plus petit pour tenir dans sa case.

Certains formulaires Adobe LiveCycle (« XFA dynamiques », souvent « Please wait… ») ne
peuvent pas être remplis ici : un bandeau explique comment les imprimer en PDF puis remplir
la copie imprimée.

### Écrire sur un formulaire plat

Pour les documents sans champs (exports Word, formulaires imprimés ou numérisés) :

- **Outil texte** (T) : cliquez et tapez. **Ctrl+Entrée** ou un clic ailleurs valide — un
  clic dans la cellule suivante y ouvre aussitôt la zone suivante. Dans une cellule, le texte
  commence au bord de la cellule ; sur une ligne « Nom : ______ », il se pose sur la ligne. Un
  aperçu en pointillés montre où il ira.
- **Tampons** : **1** ✓ coche, **2** ✗ croix, **3** ● point. Un clic dans une case centre le
  tampon et l’ajuste à la case.
- Maintenez **Alt** en cliquant pour placer exactement sous le pointeur, sans alignement.
- Cliquez sur un texte ou un tampon pour le sélectionner, faites-le glisser pour le déplacer,
  tirez une poignée pour le redimensionner, double-cliquez sur un texte pour le modifier,
  **Suppr** pour l’effacer. La taille et la couleur de la barre d’outils s’appliquent au
  texte sélectionné.

### Signer

1. **Signatures ▸ Ajouter une signature…** (ou **S** la première fois) : choisissez une photo
   ou un scan de votre signature manuscrite. Réglez le **Seuil** pour que les traits soient
   complets et le papier propre ; **Uniformiser le papier** retire les ombres, **Rogner sur
   l’encre** supprime les marges, **Couleur de l’encre** peut forcer le noir ou le bleu.
   Donnez-lui un nom.
2. Avec l’**outil signature** (S), cliquez dans la case ou sur la ligne de signature : la
   signature s’y place à la bonne taille. Ailleurs, un clic la centre, un glisser choisit sa
   largeur.
3. Déplacez-la ou redimensionnez-la comme une zone de texte. **Signatures ▸ Gérer les
   signatures…** renomme ou supprime les signatures enregistrées.

C’est une image de votre signature, pas une signature électronique certifiée.

### Surligner et copier du texte

- **Sélectionner le texte** (Maj+T) : faites glisser sur le texte, double-cliquez sur un mot,
  triple-cliquez sur une ligne, puis **Ctrl+C**.
- **Surligner** (Maj+H), **Souligner** (Maj+U), **Barrer** (Maj+S) : faites glisser sur le
  texte et relâchez.
- Cliquez sur une marque pour la sélectionner : le bouton de couleur la recolore, **Suppr**
  l’efface, Ctrl+C copie son texte.

### Modifier le texte de la page

**Modifier le texte de la page** (E) : cliquez sur un mot (un double-clic sélectionne la
partie de la ligne dans un même style), appuyez sur **Entrée** ou **F2**, tapez le nouveau
texte, puis **Entrée**. **Échap** annule. La police du document est utilisée si elle contient
les lettres tapées ; sinon une police installée proche la remplace et la barre d’état le
signale.

### Gérer les pages

Utilisez le menu **Pages** ou le clic droit sur les miniatures (**F4** affiche ou masque le
panneau). **Ctrl+clic** / **Maj+clic** sélectionnent plusieurs pages ; faites glisser les
miniatures pour réordonner ; **Suppr** supprime les pages sélectionnées. Tout s’annule.

- Insérer une page blanche (Ctrl+Maj+N) ou les pages d’un autre PDF (Ctrl+Maj+I, toutes ou
  une plage comme `1-3, 7, 10-`).
- Faire pivoter (Ctrl+R / Ctrl+Maj+R), supprimer (Ctrl+Maj+Suppr), extraire dans un nouveau
  fichier (Ctrl+Maj+E).
- **Pages ▸ Scinder le document…** découpe toutes les *N* pages ou par plages.

### Reconnaître le texte d’un scan (OCR)

Quand un document semble numérisé, un bandeau propose **Reconnaître le texte…** ; sinon
**Édition ▸ Reconnaître le texte (OCR)…** (Ctrl+Maj+O). Choisissez les pages et gardez
**Rendre le texte recherchable** coché : les mots reconnus sont ajoutés de façon invisible sur
l’image ; la page ne change pas d’aspect mais peut être recherchée, sélectionnée, copiée et
surlignée. Environ une seconde par page ; **Annuler** garde les pages déjà faites. Rien ne
quitte votre ordinateur.

Redressez d’abord les pages couchées (Ctrl+R).

### Exporter une copie

**Fichier ▸ Exporter une copie…** (Ctrl+E) écrit un nouveau fichier sans toucher au document
ouvert :

- **Aplatir les champs de formulaire** et **Aplatir les textes, tampons et signatures** les
  intègrent à la page : ils ne peuvent plus être modifiés ;
- la copie ne contient jamais les versions enregistrées antérieures (par exemple une
  signature supprimée) et peut omettre les propriétés du document.

À utiliser avant d’envoyer un document signé ou rempli.

### Raccourcis clavier

| Touches | Action |
|---|---|
| Ctrl+O / Ctrl+S / Ctrl+Maj+S | Ouvrir / Enregistrer / Enregistrer sous |
| Ctrl+E | Exporter une copie |
| Ctrl+W / Ctrl+Q | Fermer le document / Quitter |
| Ctrl+Z / Ctrl+Y | Annuler / Rétablir |
| H / F / T | Main / outil Formulaire / outil Texte |
| 1 / 2 / 3 | Tampon ✓ / ✗ / ● |
| S | Outil Signature |
| E | Modifier le texte de la page |
| Maj+T | Sélectionner le texte |
| Maj+H / Maj+U / Maj+S | Surligner / Souligner / Barrer |
| Ctrl+C | Copier le texte |
| Suppr | Supprimer l’élément sélectionné (ou les pages sélectionnées dans le panneau) |
| Alt + clic | Placer sans alignement |
| Ctrl+Entrée | Valider le texte en cours |
| Ctrl+R / Ctrl+Maj+R | Faire pivoter à droite / à gauche |
| Ctrl+Maj+N / Ctrl+Maj+I | Insérer une page blanche / des pages d’un fichier |
| Ctrl+Maj+Suppr / Ctrl+Maj+E | Supprimer / Extraire des pages |
| Ctrl+Maj+O | Reconnaître le texte (OCR) |
| Ctrl++ / Ctrl+- / Ctrl+0 | Zoom avant / arrière / 100 % |
| Ctrl+1 / Ctrl+2 | Pleine largeur / Page entière |
| Ctrl + molette | Zoom sous le pointeur |
| F4 | Afficher / masquer les miniatures |
| F1 | Raccourcis clavier |

## Captures d’écran

Voir la [version anglaise](README.md#screenshots) ; l’interface française :

![L’interface en français avec le menu Édition ouvert](docs/screenshots/interface-fr.png)

## Limites connues

- Windows uniquement.
- Les signatures sont des images de votre écriture, pas des signatures électroniques
  certifiées.
- Les formulaires XFA dynamiques (Adobe LiveCycle) ne peuvent pas être remplis : imprimez-les
  d’abord en PDF.
- Les zones de texte utilisent uniquement Helvetica (ni gras, ni italique, ni alignement).
  Les caractères hors des alphabets d’Europe occidentale peuvent ne pas s’afficher dans les
  champs et zones de texte.
- Les champs « peigne » (une lettre par case) sont remplis comme du texte simple ; les zones
  de liste n’acceptent qu’un choix.
- La modification du texte de la page porte sur une ligne et un style à la fois, sans
  recomposer le paragraphe ; si la police du document n’a pas une lettre, une police
  installée proche est utilisée.
- L’OCR ne lit que le français et l’anglais, ne détecte pas l’orientation des pages et donne
  ses meilleurs résultats sur des scans nets à 200–300 ppp (environ 9 mots sur 10) ;
  l’écriture manuscrite n’est pas reconnue.
- L’enregistrement garde les versions antérieures dans le fichier. Pour être sûr qu’un
  contenu supprimé (par exemple une signature) a vraiment disparu, utilisez **Fichier ▸
  Enregistrer sous…** ou **Fichier ▸ Exporter une copie…**.
- Les numéros de page personnalisés (i, ii, 1, 2…) ne sont pas renumérotés après un
  déplacement ou une suppression de pages, et les signets vers des pages supprimées sont
  conservés.

## Licence

PDF Editor est un logiciel libre sous [licence GNU Affero General Public License v3.0](LICENSE).
Il inclut des composants tiers listés dans [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)
(aussi dans **Aide ▸ Licences tierces…**).

## Signaler un problème

Ouvrez un [ticket](https://github.com/cor3nt1nn/pdf-editor/issues) en décrivant ce que vous
avez fait et ce qui s’est passé. Joindre le fichier journal (son emplacement est indiqué dans
**Aide ▸ À propos de PDF Editor…**) aide beaucoup.
