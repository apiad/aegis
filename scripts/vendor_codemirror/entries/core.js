// What editor.js uses of CodeMirror: one module, so every language file shares
// its state and view (two copies of @codemirror/state break the editor).
export { basicSetup } from "codemirror";
export { EditorView, keymap } from "@codemirror/view";
export { EditorState, Compartment, Prec } from "@codemirror/state";
export { indentWithTab } from "@codemirror/commands";
export {
  StreamLanguage,
  HighlightStyle,
  syntaxHighlighting,
  indentUnit,
} from "@codemirror/language";
export { tags } from "@lezer/highlight";
