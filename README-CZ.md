# Memoria

[English](README.md) | Česky

Trvalá paměť pro [Claude Code](https://claude.com/claude-code) vázaná na projekt, zabalená jako skill.

Claude si z každé session ukládá to podstatné (záznamy se zdroji, checkpoint a vlastní rozhodnutí uživatele) do prostých Markdown souborů uvnitř vašeho projektu a v další session je zase načte. Veškerou práci se soubory dělá malý deterministický skript; model jen připraví návrh.

![Jak Memoria funguje](docs/memoria-schema.svg)

*Popisky ve schématu jsou anglicky.*

## Vlastnosti

- **Jeden soubor na den a projekt**, pojmenovaný `<datum>-<projekt>.md`, např. `2026-10-15-memorizace.md`. Čitelné, prohledávatelné, srovnatelné pomocí diffu.
- **Žije v projektu**: vše je v `<projekt>/sessions/`, takže paměť putuje s repozitářem a každý projekt má vlastní historii.
- **`decisions.jsonl`**: krátký log vlastních rozhodnutí uživatele o projektu, který se načítá při každém `load`.
- **Tři režimy načtení**: poslední soubor, jeden vybraný soubor, nebo vše v pořadí uložení.
- **Dohledatelné záznamy**: každý záznam má `source`, úroveň důkazu `evidence` (`explicit_user`, `observed`, `inferred`, `assistant_proposal`) a `status`.
- **Bezpečné zápisy**: validace, zámkové soubory, kontrola revize, dočasný soubor s kontrolou round-trip, atomická výměna a zpětné načtení. Opakované uložení téhož nezmění nic, ani bajt.
- **Historie jen přidáváním**: záznam z dřívějšího dne se nikdy neupravuje; oprava je nový záznam se `supersedes: <staré id>`.

Neběží na pozadí a nemá přístup k dřívějším chatům; pamatuje si jen to, co session uloží.

## Instalace

```bash
git clone https://github.com/mkajnar/Memoria ~/.claude/skills/memoria
pip install pyyaml        # vyžaduje Python 3.10+
```

Claude Code načte `SKILL.md` a nabídne skill jako `/memoria`.

## Použití

Spouštějte Claude Code ze složky projektu; pracovní adresář určuje, kde je `sessions/`.

| Příkaz | Co dělá |
| --- | --- |
| `/memoria save` | Vytvoří nebo aktualizuje paměť z kontextu session |
| `/memoria load` | Načte **jen poslední** uložený soubor |
| `/memoria load <název>` | Načte **jeden vybraný soubor**: název projektu (`memorizace` vybere jeho nejnovější den) nebo název souboru (`2026-10-15-memorizace`) |
| `/memoria load all` | Načte **všechny soubory**, od nejstaršího po nejnovější, v pořadí, v jakém se ukládaly |

Fungují i požadavky běžnou řečí: zobrazit, navázat, opravit tvrzení, zapomenout něco. Pravidla, kterými se model řídí, jsou v [`SKILL.md`](SKILL.md) (anglicky).

## Co se ukládá

```text
<project>/sessions/
├── 2026-10-15-memorizace.md
├── 2026-10-16-memorizace.md
├── 2026-10-16-ukoly.md
└── decisions.jsonl
```

- Datum je datová část **UTC** `datetime` uložení (bez času). Uložení v 00:30 pražského času 16. se zapíše do souboru z 15.
- Název projektu je malý ASCII slug (`a-z`, `0-9`, `-`, nikoli `all`). Zadejte `--project`, jinak se použije název pracovního adresáře.

### Denní soubor

Zapisuje ho skript; ručně ho upravujte jen kvůli zapomenutí.

```markdown
---
schema_version: 2
revision: 1
updated_at: '2026-10-15T10:13:37+00:00'
scope: Memoria skill, storage layout
coverage: This conversation only
---

# memorizace — 2026-10-15

## Checkpoint

~~~yaml
goal: Ship a working memory skill
state: Script and tests are done
next: Publish the repository
~~~

## Records

### Memory lives in the project folder

~~~yaml
id: mem-9ec87ca7
title: Memory lives in the project folder
kind: decision
status: active
evidence: explicit_user
scope: storage
description: Memory is kept in ./sessions so it travels with the project and every project has its own
  history.
source: 'user: "store it in the sessions subfolder of the project"'
datetime: '2026-10-15T10:13:37+00:00'
~~~
```

`kind` je jedno z `preference`, `fact`, `decision`, `proposal`, `open_question`; `status` je `active`, `uncertain` nebo `superseded`. `datetime` je čas posledního uložení, které záznam změnilo; bere ho skript z hodin.

### decisions.jsonl

Jeden JSON objekt na řádek, vytváří ho každé úspěšné `save` (prázdný, pokud není co zapsat):

```json
{"datetime": "2026-10-15T10:13:37+00:00", "project": "memorizace", "decision": "The store lives in ./sessions", "source": "user message"}
```

Zapisují se jen rozhodnutí, která uživatel učinil sám, každé jednou krátkou větou. Pozdější rozhodnutí, které dřívější ruší, se přidá jako nový řádek; vyhrává pozdější. Přesné duplicity se přeskočí.

## Skript

`scripts/memory_store.py` je to, co skill spouští. Volejte ho ze složky projektu:

```bash
cd /path/to/project
S=~/.claude/skills/memoria/scripts/memory_store.py

python3 $S load                      # poslední soubor
python3 $S load memorizace           # nejnovější den projektu
python3 $S load 2026-10-15-memorizace
python3 $S load all                  # vše, od nejstaršího po nejnovější
python3 $S load all --query "store"  # záznamy, jejichž titulek/popis obsahuje text

python3 $S save --project memorizace --input proposal.yaml --expected-revision 0
```

`--store-dir` zvolí jinou složku úložiště místo `./sessions`.

### save

Návrhem je soubor YAML nebo JSON:

```yaml
scope: Memoria skill, storage layout
coverage: This conversation only
records:
  - kind: decision
    scope: storage
    source: 'user: "store it in the sessions subfolder of the project"'
    evidence: explicit_user
    status: active
    title: Memory lives in the project folder
    description: Memory is kept in ./sessions so it travels with the project.
checkpoint:
  goal: Ship a working memory skill
  state: Script and tests are done
  next: Publish the repository
decisions:
  - decision: The store lives in ./sessions
    source: user message
```

`--expected-revision` je `today_revisions[<projekt>]` z posledního `load`, nebo `0`, pokud projekt dnes soubor nemá. Výsledek:

```json
{
  "path": ".../sessions/2026-10-15-memorizace.md",
  "changed": true,
  "revision": 1,
  "added": ["mem-9ec87ca7"],
  "updated": [],
  "unchanged": [],
  "decisions": {"added": 1, "unchanged": 0}
}
```

| Situace | Výsledek |
| --- | --- |
| Totéž uloženo znovu | Nic se nezmění: stejné bajty, stejná revize, stejný `datetime` |
| Stejné `id`, uloženo dnes, jiný obsah | Aktualizováno; posune se jen `datetime` toho záznamu |
| Stejný obsah bez `id` (dnes nebo kterýkoli dřívější den projektu) | Přeskočeno jako duplicita |
| `id` z dřívějšího dne, jiný obsah | Odmítnuto: přidejte nový záznam se `supersedes: <id>` |
| `supersedes` ukazuje na neznámé id | Odmítnuto |
| Samotné `scope` / `coverage` | Nový soubor nevznikne; založí ho záznamy nebo checkpoint |
| Nový denní soubor bez `scope` a `coverage` | Odmítnuto |
| Špatné `--expected-revision` | Konflikt, nic se nezapíše; spusťte `load` a zkuste to znovu |
| Zastaralý soubor `.lock` | Odmítnuto, zámek se nikdy neodstraňuje automaticky |
| Chybně utvořená rozhodnutí, poškozený `decisions.jsonl`, neznámé `schema_version` | Odmítnuto, soubory zůstanou nedotčené |
| Denní soubor nad ~1 500 slov | Uloženo, s položkou `warnings` |

Chyby končí s kódem 1 a hlášením `error: ...` na stderr.

### load

`load` nikdy nezapisuje. Vypíše JSON:

| Klíč | Obsah |
| --- | --- |
| `decisions` | Celý `decisions.jsonl`, od nejstaršího po nejnovější, v každém režimu (`--query` ho neovlivní) |
| `today_revisions` | `{projekt: revize}` dnešních souborů: co předat `save` |
| `files[]` | Za každý soubor: `file`, `date`, `project`, `revision`, `updated_at`, `scope`, `coverage`, `checkpoint`, `records` |

Záznam nahrazený pozdějším nese `superseded_by`. „Poslední“ znamená uložený naposledy (podle `updated_at`), ne poslední podle názvu souboru.

## Testy

```bash
python3 -m unittest discover -s tests -v
```

78 testů: pojmenování, vykreslení a parsování (včetně nepřátelského obsahu, například bloků kódu a `---` uvnitř záznamů), validace, každé pravidlo `save` výše, log rozhodnutí, všechny tři režimy `load`, příkazová řádka a kontrola, že `SKILL.md` stále dokumentuje přepínače a rozvržení, které skript nabízí. Hodiny jsou mockované, takže se nic neuspává a půlnoc nemůže test rozházet.

## Poznámky

- `sessions/` obsahuje poznámky k projektu. Pokud je váš projekt git repozitář, rozhodněte se vědomě, zda ho commitovat; přidáním `sessions/` do `.gitignore` zůstane lokální. `.gitignore` tohoto repozitáře to už dělá.
- Skill říká modelu, aby nikdy neukládal hesla ani tokeny, ale skript to ověřit neumí.

## Struktura repozitáře

```text
SKILL.md                      skill: pravidla pro model (anglicky)
README.md                     README anglicky
README-CZ.md                  tento soubor
scripts/memory_store.py       load a save
tests/test_memory_store.py    unit testy
docs/memoria-schema.svg|png   schéma výše
```
