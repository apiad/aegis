import { StreamLanguage } from "@codemirror/language";
import { lua } from "@codemirror/legacy-modes/mode/lua";
export const lang = () => StreamLanguage.define(lua);
