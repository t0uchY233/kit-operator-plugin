#!/usr/bin/env python3
"""Расчётный сценарий: состав оператора по профилю компании-подрядчика.

Запуск:
    python3 расчёт_оператора.py профиль.json            # расчёт, вывод в Markdown
    python3 расчёт_оператора.py профиль.json --json     # тот же расчёт в JSON
    python3 расчёт_оператора.py --модель                # таблица ролей, допущений и норм
    python3 расчёт_оператора.py --пример                # образец профиля

Роли и функции лежат в `модель_расчёта.json` со ссылками на правила источников.
Норм численности в источниках нет, поэтому число позиций считается по допущениям,
записанным в той же модели, либо по нормам, которые задаёт человек. Чего посчитать
не из чего, скрипт не считает: в строке стоит «неизвестно», и в итог она не входит.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "модель_расчёта.json"
CATALOG_PATH = HERE / "знания" / "функции_оператора.md"

REQUIRED = {
    "продукт": "какой продукт оператора рассчитывается: 1, 2 или 3",
    "проектов": "сколько контрактов (проектов) подрядчика берёт в управление оператор",
    "бригад": "сколько бригад или участков работает на этих контрактах",
}
OPTIONAL = {
    "компания": "название, для заголовка",
    "площадок": "сколько площадок (мест работ); в расчёте не используется, только показывается",
    "смен_в_сутки": "сколько смен в сутки; нужно для диспетчерской функции",
    "рабочих_дней_в_неделю": "нужно для подсчёта совещаний штаба",
    "регламент_планёрок": "V02 (две планёрки по 5 минут) или V04 (одна до 15 минут); пусто — показать оба",
    "ключевые_фигуры_ставит": "оператор или клиент — кто даёт трёх ключевых руководителей проекта",
    "совмещения": "группы кодов ролей, которые ведут одни и те же люди",
    "нормы_человека": "нормы, которых нет в источниках: {ключ нормы: сколько единиц ведёт один человек}",
}
MAX_COUNT = 100_000  # больше — явная опечатка; заодно защищает деление от переполнения
UNITS = {"проектов": "проектов", "бригад": "бригад", "площадок": "площадок", "смен_в_сутки": "смен"}
METHODS = ("один", "на_единицу", "по_гипотезе_управляемости", "по_норме_человека")
SIDES = ("оператор", "клиент", "не определена")

EXAMPLE = {
    "компания": "Условный подрядчик (вводные условные — замените своими)",
    "продукт": 2,
    "проектов": 4,
    "бригад": 8,
    "площадок": 2,
    "смен_в_сутки": 2,
    "рабочих_дней_в_неделю": 6,
    "регламент_планёрок": "",
    "ключевые_фигуры_ставит": "",
    "совмещения": [],
    "нормы_человека": {},
}


class ProfileError(ValueError):
    """Профиль нельзя посчитать: не хватает данных или они неверны."""


def load_model(path: Path = MODEL_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- проверка профиля ----------


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _count_field(profile: dict, key: str, problems: list[str], required: bool) -> int | None:
    if key not in profile or profile[key] is None or profile[key] == "":
        if required:
            problems.append(f"нет данных: «{key}» — {REQUIRED[key]}")
        return None
    value = profile[key]
    if not _is_int(value) or value < 1:
        problems.append(f"«{key}» должно быть целым числом от 1, сейчас: {value!r}")
        return None
    if value > MAX_COUNT:
        problems.append(f"«{key}» больше {MAX_COUNT} — похоже на опечатку")
        return None
    return value


def _choice_field(profile: dict, key: str, allowed: list[str], problems: list[str]) -> str | None:
    value = profile.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in allowed:
        problems.append(f"«{key}»: допустимо {', '.join(allowed)} или пусто; сейчас: {value!r}")
        return None
    return value


def _usable_norm(value: object) -> bool:
    """Норма — обычное положительное число, на которое можно делить без переполнения."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and 1 / MAX_COUNT <= value <= MAX_COUNT
    except OverflowError:
        return False


def validate(profile: dict, model: dict) -> dict:
    """Проверяет профиль и возвращает нормализованную копию. Вместо недостающих данных ничего не подставляет."""
    if not isinstance(profile, dict):
        raise ProfileError("профиль — это объект JSON с полями, см. --пример")
    problems: list[str] = []
    unknown = sorted(str(k) for k in set(profile) - set(REQUIRED) - set(OPTIONAL))
    if unknown:
        problems.append("неизвестные поля профиля: " + ", ".join(unknown))

    name = profile.get("компания")
    if name not in (None, "") and not isinstance(name, str):
        problems.append("«компания» — это текст")
        name = None
    clean: dict = {"компания": name or "без названия"}

    product = profile.get("продукт")
    if product is None or product == "":
        problems.append(f"нет данных: «продукт» — {REQUIRED['продукт']}")
    elif not _is_int(product) or str(product) not in model["продукты"]:
        problems.append(f"неизвестный продукт {product!r}: в источниках три продукта — 1, 2, 3")
    else:
        clean["продукт"] = product

    for key in ("проектов", "бригад"):
        clean[key] = _count_field(profile, key, problems, required=True)
    for key in ("площадок", "смен_в_сутки", "рабочих_дней_в_неделю"):
        clean[key] = _count_field(profile, key, problems, required=False)
    if clean["рабочих_дней_в_неделю"] is not None and clean["рабочих_дней_в_неделю"] > 7:
        problems.append("«рабочих_дней_в_неделю» не может быть больше 7")

    clean["регламент_планёрок"] = _choice_field(
        profile, "регламент_планёрок", list(model["совещания"]["планёрки"]), problems
    )
    clean["ключевые_фигуры_ставит"] = _choice_field(
        profile, "ключевые_фигуры_ставит", ["оператор", "клиент"], problems
    )

    codes = {role["код"] for role in model["роли"]}
    combos = profile.get("совмещения")
    clean["совмещения"] = []
    if combos is None:
        combos = []
    if not isinstance(combos, list):
        problems.append("«совмещения» — список групп, в каждой не меньше двух кодов ролей")
        combos = []
    seen: set[str] = set()
    for group in combos:
        if not isinstance(group, list) or len(group) < 2 or not all(isinstance(c, str) for c in group):
            problems.append(f"«совмещения»: группа должна быть списком из двух и более кодов ролей, сейчас: {group!r}")
            continue
        ok = True
        for code in group:
            if code not in codes:
                problems.append(f"«совмещения»: нет роли с кодом «{code}»")
                ok = False
            elif code in seen:
                problems.append(f"«совмещения»: роль «{code}» указана дважды")
                ok = False
            seen.add(code)
        if ok:
            clean["совмещения"].append(list(group))

    norm_keys = {role["норма_человека"] for role in model["роли"] if role.get("норма_человека")}
    human = profile.get("нормы_человека")
    clean["нормы_человека"] = {}
    if human is None:
        human = {}
    if not isinstance(human, dict):
        problems.append("«нормы_человека» — словарь {ключ нормы: число}")
        human = {}
    for key, value in human.items():
        if key not in norm_keys:
            problems.append(f"«нормы_человека»: неизвестный ключ «{key}»; есть: {', '.join(sorted(norm_keys))}")
        elif not _usable_norm(value):
            problems.append(
                f"«нормы_человека.{key}» должно быть числом от {1 / MAX_COUNT:g} до {MAX_COUNT}, сейчас: {value!r}"
            )
        else:
            clean["нормы_человека"][key] = value

    if problems:
        raise ProfileError("\n".join(problems))
    return clean


# ---------- расчёт ----------


def _ceil_div(units: int, per_person: float) -> int:
    return max(1, math.ceil(units / per_person))


def _count_role(role: dict, profile: dict, span: dict) -> dict:
    """Считает одну роль. Число неизвестно — мин/база/макс равны None и указана причина."""
    method = role["способ"]
    unit_key = role.get("единица")
    units = profile.get(unit_key) if unit_key else None
    unit_name = UNITS.get(unit_key, unit_key)
    counts: tuple | None = None
    unknown = None
    kind = "задаёт человек" if method == "по_норме_человека" else "допущение"

    if method == "один":
        counts = (1, 1, 1)
        how = "одна позиция на подрядчика"
    elif method not in METHODS:
        raise ValueError(f"неизвестный способ счёта «{method}» у роли «{role['код']}»")
    elif units is None:
        how = "не посчитано"
        unknown = f"в профиле нет поля «{unit_key}»"
    elif method == "на_единицу":
        counts = (units, units, units)
        how = f"по одной на каждую единицу ({unit_name}: {units})"
    elif method == "по_гипотезе_управляемости":
        counts = tuple(_ceil_div(units, span[k]) for k in ("макс", "база", "мин"))
        how = (
            f"{unit_name}: {units} ÷ {span['база']}, округление вверх; "
            f"вилка — деление на {span['макс']} и на {span['мин']}"
        )
    else:
        norm = profile["нормы_человека"].get(role["норма_человека"])
        if norm is None:
            how = "не посчитано"
            unknown = (
                f"нет нормы «{role['норма_человека']}»: сколько {unit_name} ведёт один человек "
                f"(в профиле {unit_name}: {units}). В источниках такой нормы нет — её задаёт человек"
            )
        else:
            n = _ceil_div(units, norm)
            counts = (n, n, n)
            how = f"{unit_name}: {units} ÷ {norm:g} на человека (норму задал человек), округление вверх"

    mn, base, mx = counts if counts else (None, None, None)
    return {"мин": mn, "база": base, "макс": mx, "как": how, "неизвестно": unknown, "счёт": kind}


def _sum(rows: list[dict]) -> dict:
    known = [r for r in rows if r["база"] is not None]
    return {
        "мин": sum(r["мин"] for r in known),
        "база": sum(r["база"] for r in known),
        "макс": sum(r["макс"] for r in known),
        "строк": len(rows),
        "без_числа": len(rows) - len(known),
    }


def calculate(profile: dict, model: dict | None = None) -> dict:
    """Главная функция: профиль → состав, число позиций, проверки, пробелы."""
    model = model or load_model()
    p = validate(profile, model)
    span = model["гипотеза_управляемости"]
    product = model["продукты"][str(p["продукт"])]

    rows = []
    for role in model["роли"]:
        if p["продукт"] not in role["продукты"]:
            continue
        side = role["сторона"]
        if side == "решает человек":
            side = p["ключевые_фигуры_ставит"] or "не определена"
        row = {
            "код": role["код"],
            "название": role["название"],
            "сторона": side,
            "сторону_задал_человек": role["сторона"] == "решает человек" and side != "не определена",
            "в_источнике": role["в_источнике"],
            "основание": role["основание"],
            "допущение": role["допущение"],
            "подчиняется_руководителю": bool(role.get("подчиняется_руководителю")),
            "руководитель": bool(role.get("руководитель_команды")),
            "функции": role.get("функции", []),
            "о_функциях": role.get("о_функциях", ""),
        }
        row.update(_count_role(role, p, span))
        rows.append(row)

    by_code = {r["код"]: r for r in rows}
    groups: list[list[dict]] = []
    for group in p["совмещения"]:
        absent = [c for c in group if c not in by_code]
        if absent:
            raise ProfileError(
                f"«совмещения»: роли {', '.join(absent)} не входят в продукт {p['продукт']} — убрать из группы"
            )
        present = [by_code[c] for c in group]
        if len({r["сторона"] for r in present}) > 1:
            raise ProfileError(
                "совмещать можно только роли одной стороны: "
                + ", ".join(f"{r['код']} ({r['сторона']})" for r in present)
            )
        groups.append(present)

    totals = {side: _sum([r for r in rows if r["сторона"] == side]) for side in SIDES}

    combos = [{"роли": [r["название"] for r in present], "сторона": present[0]["сторона"]} for present in groups]

    return {
        "версия_модели": model["версия"],
        "профиль": p,
        "продукт": product,
        "гипотеза_управляемости": span,
        "состав": rows,
        "итого": totals,
        "совмещения": combos,
        "управляемость": _span_check(rows, bool(groups), span),
        "совещания": _meetings(p, model),
        "неизвестно": [{"роль": r["название"], "причина": r["неизвестно"]} for r in rows if r["неизвестно"]],
        "ключевые_фигуры_без_стороны": any(r["сторона"] == "не определена" for r in rows),
        "пробелы": model.get("пробелы", []),
        "роли_вне_расчёта": model.get("роли_вне_расчёта", []),
    }


def _span_check(rows: list[dict], has_combos: bool, span: dict) -> dict | None:
    """Сколько позиций подчинялось бы руководителю команды напрямую. Это гипотеза, не структура из источников.

    Совмещения здесь не учитываются: без распределения людей по ролям, проектам и сменам
    нельзя сказать, на сколько они уменьшают число подчинённых.
    """
    leaders = [r for r in rows if r["руководитель"] and r["сторона"] == "оператор"]
    if not leaders:
        return None
    reports = [r for r in rows if r["подчиняется_руководителю"] and r["сторона"] == "оператор"]
    direct = sum(r["база"] for r in reports if r["база"] is not None)
    unknown = sum(1 for r in reports if r["база"] is None)
    if direct > span["макс"]:
        level = "выше"
    elif direct > span["база"]:
        level = "на верхней границе"
    else:
        level = "в пределах"
    return {
        "руководитель": leaders[0]["название"],
        "позиций": direct,
        "без_числа": unknown,
        "уровень": level,
        "совмещения_не_учтены": has_combos,
    }


def _meetings(p: dict, model: dict) -> dict:
    norms = model["совещания"]
    days = p["рабочих_дней_в_неделю"]
    regimes = [p["регламент_планёрок"]] if p["регламент_планёрок"] else list(norms["планёрки"])
    daily = []
    for name in regimes:
        r = norms["планёрки"][name]
        item = {
            "регламент": name,
            "что": r["что"],
            "до": r["до"],
            "правило": r["правило"],
            "встреч_в_неделю_на_проект": None,
            "минут_в_неделю_на_проект": None,
            "минут_в_неделю_все_проекты": None,
        }
        if days is not None:
            item["встреч_в_неделю_на_проект"] = r["в_день"] * days
            item["минут_в_неделю_на_проект"] = r["в_день"] * r["минут"] * days
            item["минут_в_неделю_все_проекты"] = item["минут_в_неделю_на_проект"] * p["проектов"]
        daily.append(item)
    return {
        "ежедневные": daily,
        "выбор_регламента_за_человеком": len(daily) > 1,
        "допущение": norms["допущение"],
        "еженедельное": norms["еженедельное"],
        "ежемесячное": norms["ежемесячное"],
        "подготовка": norms["подготовка"],
        "полный_гант": norms["полный_гант"],
        "нет_данных": None
        if days is not None
        else "не указано «рабочих_дней_в_неделю» — недельная длительность встреч не посчитана",
    }


# ---------- каталог функций ----------

FIELD_RE = re.compile(r"^- ([^:]+):\s*(.*)$")


def load_functions(path: Path = CATALOG_PATH) -> list[dict]:
    """Читает раздел «1. Функции» каталога. Нет файла — пустой список."""
    if not path.exists():
        return []
    functions: list[dict] = []
    current: dict | None = None
    in_section = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_section = line.startswith("## 1.")
            current = None
            continue
        if not in_section:
            continue
        head = re.match(r"^### (Ф\d+)\.\s*(.+)$", line)
        if head:
            current = {"код": head.group(1), "название": head.group(2).strip()}
            functions.append(current)
            continue
        field = FIELD_RE.match(line)
        if current is not None and field:
            current[field.group(1).strip()] = field.group(2).strip()
    return functions


def unassigned_functions(functions: list[dict], rows: list[dict], outside: list[dict]) -> tuple[list[dict], list[dict]]:
    """Функции каталога, в которых не участвует ни одна роль расчёта.

    Первый список — источник называет исполнителем просто «оператор».
    Второй — остальные, которых нет и у ролей вне расчёта: исполнитель не назван
    либо назван только в отраслевом примере.
    """
    taken = {f["код"] for r in rows for f in r["функции"]}
    elsewhere = {f["код"] for g in outside for r in g["роли"] for f in r["функции"]}
    operator, nobody = [], []
    for f in functions:
        if f["код"] in taken:
            continue
        if "оператор" in f.get("Кто по источникам", "").lower():
            operator.append(f)
        elif f["код"] not in elsewhere:
            nobody.append(f)
    return operator, nobody


# ---------- вывод ----------


def _refs(ids: list[str]) -> str:
    return "[" + ", ".join(ids) + "]" if ids else "—"


def _count_text(r: dict) -> str:
    if r["база"] is None:
        return "неизвестно"
    if r["мин"] == r["макс"]:
        return str(r["база"])
    return f"{r['база']} (от {r['мин']} до {r['макс']})"


def _function_cell(items: list[dict], names: dict, note: str = "") -> str:
    text = "; ".join(f"{f['код']} {names.get(f['код'], '?')} — {f['часть']}" for f in items)
    if text:
        text += "."
    return " ".join(x for x in (text, note) if x) or "—"


def render_markdown(result: dict, functions: list[dict] | None = None) -> str:
    p = result["профиль"]
    product = result["продукт"]
    span = result["гипотеза_управляемости"]
    out = [f"# Расчёт оператора: {p['компания']}", ""]
    out.append(
        f"**Это расчётный сценарий, а не норма методики** (модель {result['версия_модели']}). "
        "Роли и функции взяты из материалов автора. Норм численности в материалах нет: "
        "число позиций в каждой строке получено по допущению составителя или по норме, которую задал человек. "
        "Допущения перечислены в разделе 7 и ждут решения автора методики."
    )

    out += ["", "## 1. Вводные", ""]
    out.append(f"- Продукт: {p['продукт']} — «{product['название']}», срок {product['срок']} {_refs(product['основание'])}")
    for key in ("проектов", "бригад", "площадок", "смен_в_сутки", "рабочих_дней_в_неделю"):
        out.append(f"- {key.replace('_', ' ')}: {p[key] if p[key] is not None else 'не указано'}")
    out.append(f"- ключевые фигуры проекта ставит: {p['ключевые_фигуры_ставит'] or 'не указано'}")
    out.append("- расчёт сделан на одного подрядчика")

    out += ["", "## 2. Состав", ""]
    out.append("| Роль | Что говорит источник | Сторона | Позиций | Как посчитано | Чьё это число |")
    out.append("|---|---|---|---|---|---|")
    for r in result["состав"]:
        side = r["сторона"] + (" (задал человек)" if r["сторону_задал_человек"] else "")
        out.append(
            f"| {r['название']} | {r['в_источнике']} {_refs(r['основание'])} | {side} | "
            f"{_count_text(r)} | {r['как']} | {r['счёт']} |"
        )
    out += [
        "",
        "«Допущение» — способ счёта предложил составитель модели; «задаёт человек» — число получено из нормы, "
        "которую дал человек, или не посчитано, потому что нормы нет.",
    ]

    out += ["", "## 3. Итого по сценарию", ""]
    out.append("| Сторона | Позиций посчитано | При делении на 9 | При делении на 5 | Строк без числа |")
    out.append("|---|---|---|---|---|")
    labels = {"оператор": "Команда оператора", "клиент": "У клиента", "не определена": "Сторона не определена"}
    for side in SIDES:
        t = result["итого"][side]
        if t["строк"]:
            out.append(f"| {labels[side]} | {t['база']} | {t['мин']} | {t['макс']} | {t['без_числа']} |")
    out += [
        "",
        "Как читать итог:",
        "- Это позиции (роли), а не люди. Источники не говорят, что каждая позиция — отдельный человек.",
        "- Строки без числа в сумму не входят: итог неполный, пока человек не дал нормы.",
        "- «При делении на 9» и «на 5» — тот же расчёт при других границах гипотезы 7 ± 2; это не доказанные минимум и максимум.",
    ]
    if result["ключевые_фигуры_без_стороны"]:
        out.append(
            "- Три ключевые фигуры проекта показаны отдельно: источники не говорят, кто их даёт — оператор или клиент (П25, П29)."
        )
    for c in result["совмещения"]:
        out.append(
            f"- Совмещение (задал человек): {' + '.join(c['роли'])}. Число позиций от этого не меняется. "
            "На сколько меньше будет людей, расчёт не говорит: для этого нужно знать, кто из людей какие роли, "
            "проекты и смены закрывает."
        )

    check = result["управляемость"]
    if check:
        out += ["", "## 4. Проверка по гипотезе 7 ± 2", ""]
        tail = f" и ещё {check['без_числа']} строк без числа" if check["без_числа"] else ""
        out.append(
            f"Если все позиции команды подчиняются роли «{check['руководитель']}» напрямую и каждую ведёт отдельный человек, "
            f"у неё {check['позиций']} подчинённых позиций{tail}. Это {check['уровень']} ориентира 7 ± 2 {_refs(span['основание'])}."
        )
        if check["совмещения_не_учтены"]:
            out.append(
                "Совмещения, которые задал человек, здесь не учтены: без распределения людей по ролям, проектам и сменам "
                "нельзя сказать, на сколько они уменьшают это число."
            )
        out.append(
            "Структуру подчинения источники не задают, а 7 ± 2 в источнике — про объекты внимания при разборе затрат. "
            "Поэтому это вопрос человеку, а не вывод: нужен ли промежуточный уровень или совмещение ролей."
        )

    m = result["совещания"]
    out += ["", "## 5. Совещания штаба", ""]
    out.append(f"Длительность встреч, а не трудозатраты. Допущение: {m['допущение']}.")
    out.append("")
    for d in m["ежедневные"]:
        line = f"- Регламент {d['регламент']}: {d['что']} [{d['правило']}]"
        if d["минут_в_неделю_все_проекты"] is not None:
            up = "до " if d["до"] else ""
            line += (
                f" — {d['встреч_в_неделю_на_проект']} встреч и {up}{d['минут_в_неделю_на_проект']} мин в неделю на проект, "
                f"{up}{d['минут_в_неделю_все_проекты']} мин ({up}{d['минут_в_неделю_все_проекты'] / 60:.1f} ч) на все проекты"
            )
        out.append(line)
    if m["выбор_регламента_за_человеком"]:
        out.append("- Какой регламент применять, источники не определяют (пробел П12) — выбирает человек.")
    for key, label in (("еженедельное", "Еженедельное"), ("ежемесячное", "Ежемесячное")):
        out.append(f"- {label}: не более {m[key]['минут']} мин, {m[key]['что']} [{m[key]['правило']}]")
    out.append(f"- Подготовка: {m['подготовка']['что']} [{m['подготовка']['правило']}]")
    out.append(f"- Полный план проекта видят {m['полный_гант']['человек']} человека [{m['полный_гант']['правило']}]")
    if m["нет_данных"]:
        out.append(f"- {m['нет_данных']}")

    out += ["", "## 6. Чего не хватает", ""]
    if result["неизвестно"]:
        out.append("Строки без числа:")
        out += [f"- {x['роль']}: {x['причина']}." for x in result["неизвестно"]]
        out.append("")
    out.append("Чего нет в источниках и что решает человек:")
    out += [f"- {x}" for x in result["пробелы"]]

    out += ["", "## 7. Допущения сценария", ""]
    out.append("Каждое ждёт решения автора методики. Без него числа раздела 2 нельзя считать нормой.")
    out.append("")
    out.append(f"- Гипотеза 7 ± 2: {span['что_в_источнике']} {_refs(span['основание'])}; допущение — {span['допущение']}.")
    for r in result["состав"]:
        out.append(f"- {r['название']}: {r['допущение']}")

    if functions:
        names = {f["код"]: f["название"] for f in functions}
        out += ["", "## 8. Кто в какой функции участвует", ""]
        out.append(
            "Функции — из каталога `знания/функции_оператора.md`. После тире — та часть функции, которую источник "
            "относит к этой роли. Остальная часть функции этим не закрыта: кто её делает, смотрите в каталоге — "
            "там часто стоит «в источниках не указано»."
        )
        out.append("")
        out.append("| Роль | Сторона | Функции и участие в них |")
        out.append("|---|---|---|")
        for r in result["состав"]:
            out.append(f"| {r['название']} | {r['сторона']} | {_function_cell(r['функции'], names, r['о_функциях'])} |")
        operator, nobody = unassigned_functions(functions, result["состав"], result["роли_вне_расчёта"])
        if operator:
            listed = "; ".join(f"{f['код']} {f['название']}" for f in operator)
            out += ["", f"Функции оператора, для которых источники не называют роль внутри команды: {listed}."]
        if nobody:
            listed = "; ".join(f"{f['код']} {f['название']}" for f in nobody)
            out += [
                "",
                "Функции, которые не закреплены ни за одной ролью расчёта и ни за одной ролью ниже — исполнитель "
                f"в источниках не назван либо назван только в отраслевом примере: {listed}.",
            ]
        for group in result["роли_вне_расчёта"]:
            out += ["", f"{group['группа']}:", ""]
            out.append("| Роль | Функции и участие в них | Основание |")
            out.append("|---|---|---|")
            for r in group["роли"]:
                out.append(f"| {r['название']} | {_function_cell(r['функции'], names)} | {_refs(r['основание'])} |")
    return "\n".join(out) + "\n"


def render_model(model: dict) -> str:
    span = model["гипотеза_управляемости"]
    meet = model["совещания"]
    out = ["# Параметры расчёта оператора", ""]
    out.append(
        f"Версия {model['версия']}. Файл собран командой `python3 расчёт_оператора.py --модель` "
        "из `модель_расчёта.json`; править нужно JSON, а этот файл пересобирать."
    )
    out += ["", model["пояснение"]]

    out += ["", "## Способы счёта", ""]
    out.append("| Способ | Как считается | Чьё это число |")
    out.append("|---|---|---|")
    out.append("| `один` | 1 позиция на подрядчика | допущение |")
    out.append("| `на_единицу` | столько позиций, сколько проектов или бригад | допущение |")
    out.append(
        f"| `по_гипотезе_управляемости` | число бригад ÷ {span['база']}, округление вверх; "
        f"вилка — деление на {span['макс']} и на {span['мин']} | допущение |"
    )
    out.append(
        "| `по_норме_человека` | число единиц ÷ норма, которую дал человек, округление вверх; "
        "нормы нет — «неизвестно», в итог не входит | задаёт человек |"
    )
    out += [
        "",
        f"Гипотеза 7 ± 2: {span['что_в_источнике']} {_refs(span['основание'])}. Допущение — {span['допущение']}.",
        "",
        "Итог считается отдельно по сторонам: оператор, клиент, сторона не определена. Строки «неизвестно» в сумму "
        "не входят. Три ключевые фигуры идут в ту сторону, которую назвал человек; не назвал — «сторона не определена». "
        "Совмещение ролей число позиций не меняет и в расчёт людей не переводится: для этого нужно распределение людей "
        "по ролям, проектам и сменам, которого в профиле нет. Совмещать можно только роли одной стороны.",
        "",
        "Проверка по гипотезе 7 ± 2: складываются посчитанные позиции оператора с отметкой «подчиняется руководителю»; "
        "строки без числа называются отдельно; совмещения не вычитаются. "
        "Результат больше 7 — «на верхней границе», больше 9 — «выше». Это вопрос человеку, а не вывод о структуре.",
    ]

    out += ["", "## Продукты", ""]
    out.append("| № | Продукт | Срок | Правило |")
    out.append("|---|---|---|---|")
    for key, pr in model["продукты"].items():
        out.append(f"| {key} | {pr['название']} | {pr['срок']} | {_refs(pr['основание'])} |")

    out += ["", "## Роли", ""]
    out.append("| Код | Роль | Что говорит источник | Сторона | Продукты | Способ счёта | Подчиняется руководителю | Допущение |")
    out.append("|---|---|---|---|---|---|---|---|")
    for r in model["роли"]:
        method = f"`{r['способ']}`"
        if r.get("единица"):
            method += f", единица — {UNITS.get(r['единица'], r['единица'])}"
        if r.get("норма_человека"):
            method += f", норма `{r['норма_человека']}`"
        if r.get("руководитель_команды"):
            boss = "руководитель команды"
        else:
            boss = "да" if r.get("подчиняется_руководителю") else "нет"
        out.append(
            f"| `{r['код']}` | {r['название']} | {r['в_источнике']} {_refs(r['основание'])} | {r['сторона']} | "
            f"{', '.join(map(str, r['продукты']))} | {method} | {boss} | {r['допущение']} |"
        )

    out += ["", "## Функции ролей", ""]
    out.append("Код функции — из `функции_оператора.md`. После тире — часть функции, которую источник относит к роли.")
    out.append("")
    for r in model["роли"]:
        cell = "; ".join(f"{f['код']} — {f['часть']}" for f in r.get("функции", []))
        text = " ".join(x for x in (cell + "." if cell else "", r.get("о_функциях", "")) if x)
        out.append(f"- {r['название']}: {text}")
    for group in model.get("роли_вне_расчёта", []):
        out += ["", f"{group['группа']}:"]
        for r in group["роли"]:
            cell = "; ".join(f"{f['код']} — {f['часть']}" for f in r["функции"])
            out.append(f"- {r['название']}: {cell}. {_refs(r['основание'])}")

    out += ["", "## Совещания", ""]
    out.append(f"Допущение: {meet['допущение']}. Считается длительность встреч, а не трудозатраты.")
    out.append("")
    for name, r in meet["планёрки"].items():
        out.append(f"- Регламент {name}: {r['что']} [{r['правило']}]")
    for key, label in (("еженедельное", "Еженедельное"), ("ежемесячное", "Ежемесячное")):
        out.append(f"- {label}: не более {meet[key]['минут']} мин, {meet[key]['что']} [{meet[key]['правило']}]")
    out.append(f"- Подготовка: {meet['подготовка']['что']} [{meet['подготовка']['правило']}]")
    out.append(f"- Полный план проекта видят {meet['полный_гант']['человек']} человека [{meet['полный_гант']['правило']}]")
    out.append(
        "- Минут в неделю на проект = встреч в день × минут × рабочих дней; на все проекты — умножить на число проектов. "
        "Для регламента V04 это верхняя граница («до»)."
    )

    out += ["", "## Нормы, которых в источниках нет", ""]
    out.append("Их задаёт человек в профиле, в поле `нормы_человека`. Значений по умолчанию нет.")
    out.append("")
    seen = set()
    for r in model["роли"]:
        key = r.get("норма_человека")
        if key and key not in seen:
            seen.add(key)
            out.append(f"- `{key}` — сколько {UNITS.get(r['единица'], r['единица'])} ведёт один человек")

    out += ["", "## Чего в источниках нет", ""]
    out += [f"- {x}" for x in model.get("пробелы", [])]
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    if "--пример" in flags:
        print(json.dumps(EXAMPLE, ensure_ascii=False, indent=2))
        return 0
    model = load_model()
    if "--модель" in flags:
        print(render_model(model), end="")
        return 0
    if len(args) != 1:
        print(__doc__)
        return 1
    try:
        profile = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        result = calculate(profile, model)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Не удалось прочитать профиль: {exc}", file=sys.stderr)
        return 1
    except ProfileError as exc:
        print("Расчёт не выполнен. Сначала нужно уточнить:", file=sys.stderr)
        for line in str(exc).splitlines():
            print(f"- {line}", file=sys.stderr)
        return 2
    if "--json" in flags:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(result, load_functions()), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
