import js from "@eslint/js";
import globals from "globals";

export default [
    { ignores: [".venv/"] },
    js.configs.recommended,
    {
        files: ["js/**/*.js"],
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: "script",
            globals: {
                ...globals.browser,
                // CDN globals
                L: "readonly",
                Chart: "readonly",
            },
        },
        rules: {
            eqeqeq: ["error", "always", { null: "ignore" }],
            "no-var": "error",
        },
    },
    {
        // VERSION is defined in version.js and consumed by app.js across <script> tags
        files: ["js/app.js"],
        languageOptions: {
            globals: { VERSION: "readonly" },
        },
    },
];
