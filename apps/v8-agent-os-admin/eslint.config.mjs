import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: ["src/app/**/*.{ts,tsx}", "src/components/**/*.{ts,tsx}", "src/lib/**/*.{ts,tsx}"],
    ignores: ["src/app/api/**", "src/lib/server/**", "src/lib/email.ts"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@/lib/admin-copy",
              message: "Admin UI 国际化已迁移到 JSON 词典，请改用 t('key') / createTranslator().",
            },
            {
              name: "@/lib/locale",
              importNames: ["lt", "LocalizedText", "pickLocalizedText"],
              message: "请改用 TranslationKey、t('key')、resolveText() 或 createTranslator().",
            },
          ],
        },
      ],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
