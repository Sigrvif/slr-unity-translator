// Unity-specific pattern sets for the ONE DSLR engine (v2.8; the SLRUnreal
// uePatterns.js scheme). SLR Translator is built for RPG Maker, so DSLR's
// shipped pattern families are RPG-specific (control codes \n[1], \G<...>,
// dialogue-plugin splits, script-shaped corners) — none of that occurs in
// Unity text, and what DOES occur (rich-text tags, String.Format braces) is
// not protected by them. While a Unity project is the active project, the
// DSLR engine's four pattern-family options resolve to THESE sets instead;
// every other project keeps the user's own values untouched (lib/unityEngine.js).
//
// FORMAT CONTRACT: DSLRTranslationEngineWrapper.getPatterns() wraps the stored
// string in `[\n` + str + `\n]` and evals it — so each value here must be a
// comma-separated list of REGEX LITERAL sources, one per line, with `//`
// comment lines allowed between items.
"use strict";

// --- Protected Patterns ------------------------------------------------------
// What Unity text can contain that must survive an LLM round trip:
//   - rich-text markup (<b>...</b>, <color=#f00>, <size=24>, <sprite name=x>,
//     <br/>) — Unity's own inline format, rendered by the engine
//   - String.Format / interpolation placeholders ({0}, {PlayerName},
//     {Gender|masculine}) — looked up by name/index at runtime, so a
//     translated-away argument breaks the string
//   - DSLR's own full-batch dividers (дiv markers) — engine-internal, kept
//     verbatim from the DSLR defaults so plain-text batching still recovers
// RPG-style placeholder tokens are covered by the generic brace pattern; RPG
// control codes (\n[1], \G<...>, \PICN[...]) do not occur in Unity text and
// are dropped on purpose.
const PROTECTED_PATTERNS = [
  "// SLRUnity — Unity Engine text protection",
  "// Rich-text markup: <b>, </color=#ff0000>, <size=24>, <sprite name=x/>, <br/>",
  /<\/?[A-Za-z][^<>\n]*>/g.toString() + ',',
  "// String.Format / interpolation placeholders: {0}, {PlayerName}, {Gender| masculine|feminine}",
  /\{[^{}\n]+\}/g.toString() + ',',
  "// DSLR full-batch dividers (engine-internal; verbatim from the DSLR defaults)",
  /[\n \-{]*дiv\d*[}\n \-]*/g.toString() + ',',
  /дiv/g.toString() + ',',
  /д/g.toString() + ',',
  /\n?--- {div\d+} ---\n?/g.toString() + ',',
].join("\n");

// --- Aggressive Splitting ----------------------------------------------------
// Kept from the DSLR defaults: empty-line handling and whole cells without
// Japanese (already-Latin UI labels cost tokens and risk re-translation; the
// @ in the class keeps DSLR's internal line markers out of the match).
// Dropped: every RPG Maker-specific split (system-message translations,
// dialogue plugin, CommonPopup/OSW/PSM/sac/FFF script shapes).
const SPLITTING_PATTERNS = [
  "// SLRUnity — Unity Engine splits",
  "// Empty lines",
  /^[ 　\s]*$/gm.toString() + ',',
  /^\s*[\n\r]+/gm.toString() + ',',
  /[\n\r]+\s*$/gm.toString() + ',',
  "// Cells that do not include Japanese or placeholders",
  /^[ 　]*(?!.*\\+[niv]w?\[\d+\])[^一-龠ぁ-ゔァ-ヴー々〆〤ヶﾚﾃﾞｨﾌｧｲｯ@]+[ 　]*$/gi.toString() + ',',
].join("\n");

// --- Isolation Patterns ------------------------------------------------------
// Nothing isolated by default: Unity dialogue has no nameplate-escape
// structure to peel off, and the RPG escape-driven isolations never match
// Unity text.
const ISOLATE_PATTERNS = [
  "// SLRUnity — nothing isolated by default for Unity text",
].join("\n");

// --- Cutting Corners Patterns ------------------------------------------------
// Kept from the DSLR defaults: filename-looking rows (extended with Unity
// asset extensions), lone whitespace, whitespace+linebreak corners. Dropped:
// RPG plugin/script corners and the decorative-symbol corner classes (paste
// them from the main DSLR engine's panel if you want them).
const SPLIT_ENDS_PATTERNS = [
  "// SLRUnity — Unity Engine corners",
  "// files (Unity asset extensions added)",
  /^.+\.(png|jpg|jpeg|ttf|otf|ttc|woff|woff2|txt|ini|cfg|json|js|wav|ogg|mp3|mp4|m4a|bmp|exe|assets|asset|bundle|unity|unity3d|prefab|fbx|anim|controller|mat|resource|resS|bytes).*$/gi.toString() + ',',
  "//Lone Whitespace",
  /^[ 　\s]+$/gim.toString() + ',',
  "// Whitespace with linebreak at start",
  /^[\"\'\`\’]?\s*\n\s*/gi.toString() + ',',
  "// Whitespace with linebreak at end",
  /\s*\n\s*[\"\'\`\’]?$/gi.toString() + ',',
].join("\n");

module.exports = {
  UNITY_PATTERN_DEFAULTS: {
    protectedPatterns: PROTECTED_PATTERNS,
    splittingPatterns: SPLITTING_PATTERNS,
    isolatePatterns: ISOLATE_PATTERNS,
    splitEndsPatterns: SPLIT_ENDS_PATTERNS,
  },
};
