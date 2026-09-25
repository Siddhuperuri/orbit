import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

/**
 * ESLint 9 flat config.
 *
 * eslint-config-next 16 ships native flat config, so it is spread directly.
 * The `FlatCompat` bridge that older guides show is not only unnecessary here,
 * it fails outright against these packages.
 */
const config = [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "out/**",
      "next-env.d.ts",
      // Generated from the backend's OpenAPI schema; not hand-edited.
      "lib/api/generated.ts",
      "lib/api/access.ts",
      // Build and report output: the e2e suite's own Next build directory and
      // the coverage reporter's. Both are regenerated, neither is source.
      ".next-e2e/**",
      "coverage/**",
    ],
  },

  ...nextCoreWebVitals,
  ...nextTypeScript,

  {
    rules: {
      // `any` defeats the strict typing the brief requires. A warning rather
      // than an error so it can be used deliberately at a genuine boundary,
      // while still surfacing in review.
      "@typescript-eslint/no-explicit-any": "warn",

      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],

      // Rendering unsanitised HTML is how a malicious document becomes stored
      // XSS. Document text is always rendered as text
      // (docs/architecture/security.md).
      "react/no-danger": "error",

      // console output is unstructured and reaches no log pipeline.
      "no-console": ["error", { allow: ["warn", "error"] }],

      // `== null` is the one intentional loose comparison.
      eqeqeq: ["error", "always", { null: "ignore" }],
    },
  },
];

export default config;
