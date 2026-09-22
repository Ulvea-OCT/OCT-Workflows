# OCT-Workflows

`OCT-Workflows` è il repository centrale di `Ulvea-OCT` per la logica GitHub Actions condivisa.

## Struttura

```text
OCT-Workflows/
├── .github/
│   ├── workflows/
│   │   ├── roadmap.yml
│   │   └── ...
│   └── actions/
│       ├── roadmap/
│       │   ├── action.yml
│       │   └── scripts/
│       │       ├── parse-roadmap.py
│       │       ├── schedule-roadmap.py
│       │       └── apply-roadmap.py
│       └── ...
└── README.md
```

## Principio

| Componente | Responsabilità |
|---|---|
| `OCT-Workflows` | Logica condivisa |
| `OCT-Template` | Bootstrap dei nuovi progetti |
| Repository progetto | Codice e configurazione |
| Workflow specifico | Logica esclusiva |

Gli script non vengono copiati nei progetti.

## Reusable workflows

Esempio:

```yaml
jobs:
  roadmap:
    uses: Ulvea-OCT/OCT-Workflows/.github/workflows/roadmap.yml@v1
    secrets: inherit
```

Il repository chiamante rimane il target dell'operazione.

## Roadmap

Componenti:

```text
.github/workflows/roadmap.yml
.github/actions/roadmap/
├── action.yml
└── scripts/
    ├── parse-roadmap.py
    ├── schedule-roadmap.py
    └── apply-roadmap.py
```

Pipeline:

```text
roadmap.md
  ↓
parse-roadmap.py
  ↓
schedule-roadmap.py
  ↓
apply-roadmap.py
  ↓
GitHub Issues
  ↓
GitHub Project
```

### Parser

`parse-roadmap.py` legge la roadmap Markdown, valida i metadati e produce la struttura delle attività.

### Scheduler

`schedule-roadmap.py` calcola la pianificazione rispettando dipendenze, durata, date e parallelismo.

### Applier

`apply-roadmap.py` gestisce Issues, labels, Project organizzativo, custom fields, date, dipendenze e viste.

## Versionamento

I progetti devono usare una versione stabile:

```yaml
uses: Ulvea-OCT/OCT-Workflows/.github/workflows/roadmap.yml@v1
```

Evitare `@main` nei progetti.

Le modifiche incompatibili devono poter essere pubblicate in una nuova major, ad esempio `v2`.

## Nuovi workflow

Un workflow comune va in:

```text
.github/workflows/<workflow>.yml
```

e dovrebbe essere un reusable workflow quando deve essere usato da più repository.

## Nuove actions

Le composite actions comuni vanno sotto:

```text
.github/actions/
```

## Workflow specifici

La logica esclusiva di un singolo progetto rimane nella repository del progetto.

## Configurazione

Non hard-codificare i nomi dei progetti.

Variabili Roadmap previste:

```text
ROADMAP_PROJECT_OWNER
ROADMAP_PROJECT_OWNER_TYPE
```

Il repository target è:

```text
github.repository
```

## Secrets

Secret previsto:

```text
ROADMAP_PROJECT_TOKEN
```

Il caller può usare `secrets: inherit`.

Il secret distribuisce il token ma non ne modifica i permessi.

## Regola

Se una logica deve essere condivisa da più repository → `OCT-Workflows`.

Se è esclusiva di un progetto → repository del progetto.

## Rapporto con `OCT-Template`

Il template contiene i caller necessari al bootstrap. Gli script rimangono esclusivamente in `OCT-Workflows`.

## Retroattività

Modificare `OCT-Template` non modifica automaticamente i repository già creati.

La logica comune deve quindi vivere in `OCT-Workflows`. Un eventuale sistema di sincronizzazione dei file locali è una funzionalità futura.
