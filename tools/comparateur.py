#!/usr/bin/env python3
"""Génère dashboards/comparateur-offres.json (dashboard Grafana « Comparateur d'offres »).

Les requêtes partagent un même socle SQL (CORE) : courbe de charge de la période, catégories
horaires (HC de l'utilisateur, HC Tempo, week-end, hiver, nuit), offres actuelles, coût par offre.

    python3 tools/comparateur.py            # écrit le dashboard
    python3 tools/comparateur.py --sql      # affiche les requêtes (pour les tester avec psql)
"""

import json
import sys
from pathlib import Path

DS = {"type": "grafana-postgresql-datasource", "uid": "${DS_MED}"}
TYPES = "('BASE', 'HC_HP', 'WEEKEND', 'HC_NUIT_WEEKEND', 'SEASONAL', 'TEMPO')"


def core(by_month: bool = False) -> str:
    """Socle commun. by_month : coûts aussi ventilés par mois (graphique mensuel)."""
    m = "date_trunc('month', date)::date AS mois_d, " if by_month else ""
    mg = ", mois_d" if by_month else ""
    return f"""WITH pts AS (
  -- Courbe de charge : valeur = puissance moyenne (W) sur le pas, pas de 30 ou 15 min selon le jour
  SELECT c.date,
    c.interval_start::time AS t,
    extract(isodow FROM c.date)::int AS dow,
    extract(month FROM c.date)::int AS mois,
    ((c.date + c.interval_start::time) - interval '6 hours')::date AS tempo_date,
    c.value / 1000.0 * 24.0 / count(*) OVER (PARTITION BY c.usage_point_id, c.date) AS kwh
  FROM consumption_data c
  WHERE c.granularity = 'detailed'
    AND c.date BETWEEN ($__timeFrom()::timestamptz AT TIME ZONE 'Europe/Paris')::date
                   AND ($__timeTo()::timestamptz AT TIME ZONE 'Europe/Paris')::date
), saisie AS (
  -- Plages HC Enedis de l'utilisateur, ex. 22:00-06:00 ou 01:30-07:30;12:30-14:30
  SELECT split_part(p, '-', 1)::time AS deb, split_part(p, '-', 2)::time AS fin
  FROM unnest(string_to_array(replace('$plages_hc', ' ', ''), ';')) AS p
  WHERE p LIKE '%-%'
), plages AS (
  SELECT deb, fin FROM saisie
  UNION ALL SELECT '22:00'::time, '06:00'::time WHERE NOT EXISTS (SELECT 1 FROM saisie)
), cat AS (
  -- Les points sont regroupés par catégorie horaire : le calcul par offre reste léger
  SELECT {m}
    EXISTS (SELECT 1 FROM plages pl
            WHERE (pl.deb < pl.fin AND x.t >= pl.deb AND x.t < pl.fin)
               OR (pl.deb >= pl.fin AND (x.t >= pl.deb OR x.t < pl.fin))) AS hc,
    (x.t >= '22:00' OR x.t < '06:00') AS hc_tempo,
    (x.t >= '23:00' OR x.t < '06:00') AS nuit,
    x.dow >= 6 AS we,
    x.mois IN (11, 12, 1, 2, 3) AS hiver,
    COALESCE(td.color::text, 'BLUE') AS couleur,
    sum(x.kwh) AS kwh
  FROM pts x
  LEFT JOIN tempo_days td ON td.id = to_char(x.tempo_date, 'YYYY-MM-DD')
  GROUP BY 1, 2, 3, 4, 5, 6{", 7" if by_month else ""}
), info AS (
  SELECT count(DISTINCT date) AS jours, sum(kwh) AS kwh, min(date) AS debut, max(date) AS fin FROM pts
), offres AS (
  -- Grille actuelle de chaque offre à la puissance choisie
  SELECT DISTINCT ON (ep.name, eo.name, eo.offer_type) eo.*, ep.name AS fournisseur
  FROM energy_offers eo
  JOIN energy_providers ep ON ep.id = eo.provider_id
  WHERE eo.power_kva = $puissance_kva AND eo.is_active AND ep.is_active
    AND eo.offer_type IN {TYPES}
    AND (eo.valid_from IS NULL OR eo.valid_from <= now())
  ORDER BY ep.name, eo.name, eo.offer_type, (eo.valid_to IS NULL OR eo.valid_to > now()) DESC,
           eo.valid_from DESC NULLS LAST
), prix AS (
  SELECT o.fournisseur, o.name AS offre, o.offer_type, o.subscription_price{mg},
    cat.kwh,
    CASE o.offer_type
      WHEN 'BASE' THEN CASE WHEN cat.we THEN COALESCE(o.base_price_weekend, o.base_price) ELSE o.base_price END
      WHEN 'HC_NUIT_WEEKEND' THEN CASE WHEN cat.we OR cat.nuit THEN o.hc_price ELSE o.hp_price END
      WHEN 'SEASONAL' THEN CASE WHEN cat.hiver
        THEN CASE WHEN cat.hc THEN o.hc_price_winter ELSE o.hp_price_winter END
        ELSE CASE WHEN cat.hc THEN o.hc_price_summer ELSE o.hp_price_summer END END
      WHEN 'TEMPO' THEN CASE cat.couleur
        WHEN 'RED'   THEN CASE WHEN cat.hc_tempo THEN o.tempo_red_hc   ELSE o.tempo_red_hp   END
        WHEN 'WHITE' THEN CASE WHEN cat.hc_tempo THEN o.tempo_white_hc ELSE o.tempo_white_hp END
        ELSE              CASE WHEN cat.hc_tempo THEN o.tempo_blue_hc  ELSE o.tempo_blue_hp  END END
      ELSE -- HC_HP et WEEKEND : HC/HP, avec des prix week-end éventuels
        CASE WHEN cat.we
          THEN CASE WHEN cat.hc THEN COALESCE(o.hc_price_weekend, o.hc_price) ELSE COALESCE(o.hp_price_weekend, o.hp_price) END
          ELSE CASE WHEN cat.hc THEN o.hc_price ELSE o.hp_price END END
    END AS prix
  FROM offres o CROSS JOIN cat
), couts AS (
  -- Une offre dont la grille est incomplète est écartée plutôt que sous-estimée
  SELECT fournisseur, offre, offer_type, subscription_price{mg}, sum(kwh * prix) AS energie
  FROM prix
  GROUP BY fournisseur, offre, offer_type, subscription_price{mg}
  HAVING bool_and(prix IS NOT NULL)
)"""


def type_label(col="offer_type"):
    return f"""CASE {col} WHEN 'BASE' THEN 'Base' WHEN 'HC_HP' THEN 'Heures creuses' WHEN 'WEEKEND' THEN 'Week-end'
    WHEN 'HC_NUIT_WEEKEND' THEN 'HC nuit et week-end' WHEN 'SEASONAL' THEN 'Saisonnière' WHEN 'TEMPO' THEN 'Tempo'
    ELSE {col} END"""


def nom(f="fournisseur", o="offre", t="offer_type"):
    # Un fournisseur peut proposer une offre du même nom en Base et en HC : le type fait partie de la clé
    return f"{f} || ' – ' || {o} || ' (' || {type_label(t)} || ')'"


TYPE_LABEL = type_label()


ANNUEL = """, annuel AS (
  SELECT """ + nom("c.fournisseur", "c.offre", "c.offer_type") + """ AS nom, c.fournisseur, c.offre, c.offer_type,
    c.subscription_price * 12 AS abo_an,
    c.energie * 365.0 / NULLIF(i.jours, 0) AS energie_an,
    c.subscription_price * 12 + c.energie * 365.0 / NULLIF(i.jours, 0) AS total_an
  FROM couts c, info i
)"""


ACTUELLE = "(SELECT min(total_an) FROM annuel WHERE nom = '$offre_actuelle')"

Q = {
    "jours": core() + "\nSELECT jours AS \"Jours\" FROM info",
    "conso": core() + "\nSELECT kwh * 365.0 / NULLIF(jours, 0) AS \"Consommation / an\" FROM info",
    "actuelle": core() + ANNUEL + f"\nSELECT {ACTUELLE} AS \"Offre actuelle\"",
    "meilleure_nom": core() + ANNUEL + "\nSELECT nom AS \"offre\" FROM annuel ORDER BY total_an LIMIT 1",
    "meilleure": core() + ANNUEL + "\nSELECT min(total_an) AS \"Meilleure offre\" FROM annuel",
    "economie": core() + ANNUEL + f"\nSELECT {ACTUELLE} - min(total_an) AS \"Économie possible\" FROM annuel",
    "classement": core() + ANNUEL + f"""
SELECT rank() OVER (ORDER BY total_an) AS "Rang",
  fournisseur AS "Fournisseur", offre AS "Offre", {TYPE_LABEL} AS "Type",
  abo_an AS "Abonnement / an", energie_an AS "Énergie / an", total_an AS "Total / an",
  total_an - {ACTUELLE} AS "Écart avec l'offre actuelle",
  CASE WHEN nom = '$offre_actuelle' THEN 'Offre actuelle' ELSE '' END AS "Note"
FROM annuel
ORDER BY total_an""",
    "top": core() + ANNUEL + """
SELECT nom AS "Offre", total_an AS "Total / an"
FROM annuel ORDER BY total_an LIMIT 15""",
    "par_type": core() + ANNUEL + f"""
SELECT DISTINCT ON (offer_type) {TYPE_LABEL} AS "Type", fournisseur AS "Fournisseur", offre AS "Offre",
  total_an AS "Total / an", total_an - {ACTUELLE} AS "Écart avec l'offre actuelle"
FROM annuel
ORDER BY offer_type, total_an""",
    "mensuel": core(by_month=True) + ANNUEL.replace(
        "FROM couts c, info i", "FROM (SELECT fournisseur, offre, offer_type, subscription_price, sum(energie) AS energie FROM couts GROUP BY 1, 2, 3, 4) c, info i"
    ) + """, meilleure AS (SELECT nom FROM annuel ORDER BY total_an LIMIT 1)
SELECT mois_d::timestamp AT TIME ZONE 'Europe/Paris' AS time,
  sum(energie + subscription_price) FILTER (WHERE """ + nom() + """ = '$offre_actuelle') AS "Offre actuelle",
  sum(energie + subscription_price) FILTER (WHERE """ + nom() + """ = (SELECT nom FROM meilleure)) AS "Meilleure offre"
FROM couts
GROUP BY 1
ORDER BY 1""",
}

VAR_OFFRES = f"""SELECT DISTINCT {nom("ep.name", "eo.name", "eo.offer_type")}
FROM energy_offers eo JOIN energy_providers ep ON ep.id = eo.provider_id
WHERE eo.power_kva = $puissance_kva AND eo.is_active AND ep.is_active AND eo.offer_type IN {TYPES}
ORDER BY 1"""


def target(sql):
    return [{"refId": "A", "datasource": DS, "format": "table", "rawQuery": True, "editorMode": "code", "rawSql": sql}]


def euro(decimals=0):
    return {"unit": "currencyEUR", "decimals": decimals}


def stat(title, sql, pos, unit=None, color="text", fields="", desc="", no_value="—"):
    defaults = {"color": {"mode": "fixed", "fixedColor": color}, "noValue": no_value}
    if unit:
        defaults.update(unit)
    return {
        "type": "stat", "title": title, "description": desc, "gridPos": pos, "datasource": DS,
        "targets": target(sql),
        "options": {"reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": fields},
                    "colorMode": "value", "graphMode": "none", "textMode": "value", "justifyMode": "center",
                    "wideLayout": True},
        "fieldConfig": {"defaults": defaults, "overrides": []},
    }


def money_override(name, decimals=0):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "unit", "value": "currencyEUR"}, {"id": "decimals", "value": decimals}]}


def dashboard():
    p = []
    p.append(stat("Jours de courbe de charge", Q["jours"], {"x": 0, "y": 0, "w": 4, "h": 4},
                  desc="Jours de la période choisie couverts par la courbe de charge Enedis. Les coûts sont ramenés à "
                       "une année : avec moins de 12 mois, le résultat dépend des saisons couvertes.",
                  unit={"unit": "none", "decimals": 0,
                        "thresholds": {"mode": "absolute", "steps": [
                            {"color": "orange", "value": None}, {"color": "green", "value": 330}]}},
                  color="text"))
    p[-1]["fieldConfig"]["defaults"]["color"] = {"mode": "thresholds"}
    p.append(stat("Consommation ramenée à l'année", Q["conso"], {"x": 4, "y": 0, "w": 4, "h": 4},
                  unit={"unit": "kwatth", "decimals": 0}))
    p.append(stat("Votre offre actuelle (par an)", Q["actuelle"], {"x": 8, "y": 0, "w": 4, "h": 4},
                  unit=euro(), color="blue", no_value="Choisissez votre offre",
                  desc="Offre choisie dans la liste « Offre actuelle » en haut du dashboard, abonnement compris."))
    p.append(stat("Meilleure offre", Q["meilleure_nom"], {"x": 12, "y": 0, "w": 4, "h": 4},
                  fields="/^offre$/", color="green"))
    p[-1]["options"]["text"] = {"valueSize": 18}
    p.append(stat("Coût de la meilleure offre (par an)", Q["meilleure"], {"x": 16, "y": 0, "w": 4, "h": 4},
                  unit=euro(), color="green"))
    p.append(stat("Économie possible (par an)", Q["economie"], {"x": 20, "y": 0, "w": 4, "h": 4},
                  unit=euro(), color="green", no_value="Choisissez votre offre"))

    p.append({
        "type": "table", "title": "Classement des offres (coût ramené à un an, abonnement compris)",
        "gridPos": {"x": 0, "y": 4, "w": 14, "h": 16}, "datasource": DS, "targets": target(Q["classement"]),
        "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
        "fieldConfig": {
            "defaults": {"custom": {"align": "auto", "filterable": True}},
            "overrides": [
                money_override("Abonnement / an"), money_override("Énergie / an"), money_override("Total / an"),
                {"matcher": {"id": "byName", "options": "Total / an"},
                 "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                {"id": "color", "value": {"mode": "continuous-GrYlRd"}}]},
                {"matcher": {"id": "byName", "options": "Écart avec l'offre actuelle"},
                 "properties": [{"id": "unit", "value": "currencyEUR"}, {"id": "decimals", "value": 0},
                                {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                                    {"color": "green", "value": None}, {"color": "text", "value": -0.5},
                                    {"color": "red", "value": 0.5}]}},
                                {"id": "color", "value": {"mode": "thresholds"}}]},
                {"matcher": {"id": "byName", "options": "Rang"}, "properties": [{"id": "custom.width", "value": 60}]},
                {"matcher": {"id": "byName", "options": "Note"},
                 "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                {"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}]},
            ],
        },
    })
    p.append({
        "type": "barchart", "title": "Les 15 offres les moins chères (par an)",
        "gridPos": {"x": 14, "y": 4, "w": 10, "h": 16}, "datasource": DS, "targets": target(Q["top"]),
        "options": {"orientation": "horizontal", "xField": "Offre", "showValue": "auto", "barWidth": 0.7,
                    "legend": {"showLegend": False}, "tooltip": {"mode": "single"}, "xTickLabelMaxLength": 40},
        "fieldConfig": {"defaults": {**euro(), "color": {"mode": "continuous-GrYlRd"},
                                     "custom": {"axisSoftMin": 0, "fillOpacity": 85}}, "overrides": []},
    })
    p.append({
        "type": "table", "title": "Meilleure offre par type",
        "gridPos": {"x": 0, "y": 20, "w": 12, "h": 8}, "datasource": DS, "targets": target(Q["par_type"]),
        "options": {"showHeader": True, "cellHeight": "sm"},
        "fieldConfig": {"defaults": {}, "overrides": [
            money_override("Total / an"),
            {"matcher": {"id": "byName", "options": "Écart avec l'offre actuelle"},
             "properties": [{"id": "unit", "value": "currencyEUR"}, {"id": "decimals", "value": 0},
                            {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                            {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                                {"color": "green", "value": None}, {"color": "text", "value": -0.5},
                                {"color": "red", "value": 0.5}]}},
                            {"id": "color", "value": {"mode": "thresholds"}}]},
        ]},
    })
    p.append({
        "type": "barchart", "title": "Coût mois par mois : offre actuelle et meilleure offre",
        "gridPos": {"x": 12, "y": 20, "w": 12, "h": 8}, "datasource": DS, "targets": target(Q["mensuel"]),
        "options": {"orientation": "vertical", "xField": "time", "showValue": "never", "groupWidth": 0.7,
                    "barWidth": 0.9, "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"}, "xTickLabelRotation": 0, "xTickLabelSpacing": 0},
        "fieldConfig": {"defaults": {**euro(), "min": 0}, "overrides": [
            {"matcher": {"id": "byName", "options": "Offre actuelle"},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}]},
            {"matcher": {"id": "byName", "options": "Meilleure offre"},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]},
            {"matcher": {"id": "byName", "options": "time"},
             "properties": [{"id": "unit", "value": "time:MMM YYYY"}]},
        ]},
    })
    p.append({
        "type": "text", "title": "Comment c'est calculé", "gridPos": {"x": 0, "y": 28, "w": 24, "h": 6},
        "options": {"mode": "markdown", "content": (
            "- **Votre consommation réelle** : la courbe de charge Enedis (pas de 30 ou 15 minutes) sur la période "
            "choisie en haut à droite, **ramenée à une année**. Avec moins de 12 mois de données, le résultat dépend "
            "des saisons couvertes (l'hiver pèse lourd en Tempo).\n"
            "- **Les tarifs actuels** de toutes les offres de la base MyElectricalData à votre puissance, abonnement "
            "compris (TTC). Une offre à la grille incomplète est écartée ; l'EJP n'est pas comparée (jours EJP inconnus).\n"
            "- **Heures creuses** : vos plages Enedis (variable « Plages HC », visibles sur votre facture ou dans "
            "l'interface MyElectricalData) pour les offres HC ; 22 h - 6 h pour Tempo ; 23 h - 6 h et tout le week-end "
            "pour les offres « nuit et week-end ». **Tempo** : les vraies couleurs des jours de la période.\n"
            "- C'est une **estimation pour comparer** : les prix évoluent, et votre consommation changerait peut-être "
            "avec une autre offre (décaler des usages en heures creuses, éviter les jours rouges…).")},
    })

    return {
        "__inputs": [{"name": "DS_MED", "label": "MyElectricalData new",
                      "description": "Source PostgreSQL de l'add-on MyElectricalData new", "type": "datasource",
                      "pluginId": "grafana-postgresql-datasource", "pluginName": "PostgreSQL"}],
        "title": "Comparateur d'offres",
        "uid": "med-comparateur-offres",
        "description": "Combien vous aurait coûté votre consommation réelle avec chaque offre (MyElectricalData new)",
        "tags": ["myelectricaldata", "linky", "tarifs"],
        "timezone": "Europe/Paris",
        "time": {"from": "now-1y", "to": "now"},
        "editable": True,
        "schemaVersion": 39,
        "templating": {"list": [
            {"type": "textbox", "name": "puissance_kva", "label": "Puissance souscrite (kVA)", "query": "6",
             "current": {"text": "6", "value": "6"}, "hide": 0},
            {"type": "textbox", "name": "plages_hc", "label": "Plages HC (ex. 22:00-06:00 ou 01:30-07:30;12:30-14:30)",
             "query": "22:00-06:00", "current": {"text": "22:00-06:00", "value": "22:00-06:00"}, "hide": 0},
            {"type": "query", "name": "offre_actuelle", "label": "Offre actuelle", "datasource": DS,
             "query": VAR_OFFRES, "definition": VAR_OFFRES, "refresh": 1, "sort": 0, "multi": False,
             "includeAll": False, "hide": 0},
        ]},
        "panels": p,
    }


if __name__ == "__main__":
    if "--sql" in sys.argv:
        print(json.dumps({**Q, "var_offres": VAR_OFFRES}, ensure_ascii=False))
    else:
        out = Path(__file__).resolve().parent.parent / "dashboards" / "comparateur-offres.json"
        out.write_text(json.dumps(dashboard(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Écrit : {out}")
