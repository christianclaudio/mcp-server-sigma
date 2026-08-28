---
last_verified: 2026-08-01
sigma_docs_url: https://help.sigmacomputing.com/docs/functions
---

# Sigma Formula Pitfalls — Curated Reference

> **This is a curated list of common mistakes when writing Sigma formulas, NOT a
> complete function reference.** For the full function list, see:
> <https://help.sigmacomputing.com/docs/functions>

---

## Column Reference Syntax

- All column references use square brackets: `[Column Name]`
- Spaces and special characters in column names are fine inside brackets: `[Total Revenue ($)]`
- Cross-element references use the element name prefix: `[Element Name/Column Name]`
- **Pitfall:** Using bare names without brackets (e.g., `ColumnName`) will not resolve — always bracket.

## Path Access into VARIANT / JSON Columns

- Use bracket-dot notation to reach nested fields: `[JsonColumn].path.to.field`
- Array indexing uses `[n]` inside the path: `[JsonColumn].items[0].name`
- **Type requirement:** The source column MUST be typed as VARIANT/OBJECT/ARRAY in the warehouse. If the column is stored as TEXT (even if its content is JSON), path access silently returns NULL or errors.

### Real Bug Example

A metric formula `[Metrics][0].key.metric` was defined against a column that the warehouse stored as TEXT (VARCHAR). Even though the text content was valid JSON, Sigma's path access requires the column to be typed ARRAY or VARIANT. The formula silently broke — no error, just NULL results.

**Fix:** Either:
1. Cast the warehouse column to VARIANT/ARRAY/OBJECT at the source, or
2. Use `Json([TextColumn])[0].key.metric` or `Variant([TextColumn])[0].key.metric` in Sigma to convert the text before path access.

## Type Coercion and Casting

- Sigma does implicit coercion in some contexts (e.g., number + text-that-looks-like-number may work) but this is **unreliable across warehouses.**
- **Always cast explicitly** when types don't match:
  - `Number([text_column])` — text to number
  - `Text([number_column])` — number to text
  - `DateParse([text_column], "%Y-%m-%d")` — text to date
- **Pitfall:** Assuming `"123" + 1` works everywhere. It may in some warehouse backends but not others.

## NULL Handling

- **Aggregates ignore NULLs:** `Sum([Column])` skips NULL rows (standard SQL semantics).
- **Row-level expressions propagate NULL:** `[A] + [B]` is NULL if either A or B is NULL.
- Use `Coalesce([Column], 0)` or `If(IsNull([Column]), default, [Column])` to handle NULLs in row-level formulas.
- **Pitfall:** Expecting `CountIf([Column] != "X")` to count NULL rows — it won't. NULLs fail all comparisons.

## Aggregate vs Row-Level Context

- **Row-level functions** operate per row: `If`, `Contains`, `Left`, `DateDiff`, math operators.
- **Aggregate functions** collapse rows: `Sum`, `Avg`, `Count`, `CountIf`, `Min`, `Max`.
- **You cannot nest an aggregate inside another aggregate** — e.g., `Sum(Avg([X]))` is invalid.
- **You cannot use a row-level reference inside a metric** without wrapping in an aggregate.
- **Error you'll see:** "Cannot mix aggregate and non-aggregate expressions" — this means you used a bare column reference where an aggregate is required, or vice versa.

## Date Function Argument Order

Date functions in Sigma follow the pattern `DateDiff(unit, start, end)` — the **unit comes first**.

```
-- CORRECT:
DateDiff("day", [Start Date], [End Date])

-- WRONG (common LLM error — putting dates before unit):
DateDiff([Start Date], [End Date], "day")   -- ERROR
```

- `DateAdd(unit, amount, date)` — unit first, then how many, then the date.
- `DateTrunc(unit, date)` — unit first, then date.
- **Pitfall:** Many SQL dialects use `DATEDIFF(start, end)` without a unit, or put the unit last. Sigma always puts the unit first.

## Metrics vs Calculated Columns

| | Calculated Column | Metric |
|---|---|---|
| **Defined at** | Row level | Aggregate level |
| **Evaluated** | Per row, before grouping | After GROUP BY, at display time |
| **Can reference** | Other columns in same element | Columns and other metrics |
| **Use for** | Derived row data (concatenation, flags, parsed fields) | KPIs, ratios, aggregations |

### Why Ratio-of-Sums Matters

When computing a ratio metric (e.g., conversion rate), define it as:

```
Sum([Conversions]) / Sum([Sessions])
```

**NOT** as `Avg([Conversion Rate Per Row])` — the average-of-ratios gives wrong results when row counts differ across groups. The sum-of-sums form aggregates correctly at any hierarchy level (region → store → day).

## If Unsure, Do This

1. **Check the official docs** — <https://help.sigmacomputing.com/docs/functions>
2. **Verify column types** before using path access or implicit coercion.
3. **Test with a small dataset** — create a workbook page with your formula on a few rows first.
4. **Use explicit casts** rather than relying on implicit coercion.
5. **Prefer metrics for any aggregation** — don't compute aggregates in calculated columns unless you need a window function.
