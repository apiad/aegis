import { StreamLanguage } from "@codemirror/language";
import { toml } from "@codemirror/legacy-modes/mode/toml";
export const lang = () => StreamLanguage.define(toml);
