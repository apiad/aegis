import { StreamLanguage } from "@codemirror/language";
import { perl } from "@codemirror/legacy-modes/mode/perl";
export const lang = () => StreamLanguage.define(perl);
