# DeepSeek Settings Design

## Goal

Make DeepSeek the only configurable LLM provider in the current Paper2Post UI so a new developer can enter a repository-specific API key and immediately use an official DeepSeek model. The default model is `deepseek-v4-flash-vision-exp`.

## Scope

- The settings page exposes a DeepSeek API key password field and model selector.
- The official DeepSeek endpoint is fixed to `https://api.deepseek.com`.
- The provider is fixed to `deepseek`; the UI does not expose provider or base URL controls.
- The model selector contains:
  - `deepseek-v4-flash-vision-exp` (default)
  - `deepseek-v4-flash`
  - `deepseek-v4-pro`
  - a custom model ID option that still uses the fixed DeepSeek endpoint
- Existing keys are preserved when the password field is submitted empty.
- Secrets are written only to the Git-ignored project `.env` file and are never returned to the browser.

## Backend

The LLM registry owns the DeepSeek endpoint, default model, and selectable model catalog. Settings APIs return only the model catalog, selected model, and a boolean indicating whether `DEEPSEEK_API_KEY` is configured.

Saving settings writes `DEEPSEEK_API_KEY` when a non-empty key is supplied and persists the selected model in the Git-ignored `data/settings.json`. Generation always constructs the provider as DeepSeek and does not accept a client-supplied provider or base URL.

## Frontend

The existing settings layout and visual language remain unchanged. Provider and Base URL controls are removed. The model text field becomes a selector. Selecting the custom option reveals a model ID input. The key field remains a password input and never displays a stored secret.

The generation form uses the saved/default DeepSeek model. It does not place the API key in the query string.

## Error Handling

- Missing DeepSeek keys are reported as unconfigured rather than silently represented as another provider.
- Empty key submissions retain the current key.
- Unsupported empty custom model IDs are rejected by the settings endpoint.
- This feature does not change the existing pipeline fallback behavior; removing silent Mock fallback belongs to the pipeline reliability milestone.

## Tests

- DeepSeek is the only advertised provider.
- The vision model is the default and the three official models are advertised.
- `DEEPSEEK_API_KEY` is detected correctly.
- Saving a non-empty key writes it locally without returning it.
- Saving an empty key preserves the existing value.
- Custom model IDs remain bound to the DeepSeek endpoint.
- Frontend source no longer renders provider or base URL controls.

## Out Of Scope

- Additional LLM providers or custom provider endpoints
- Sending extracted figures to the vision model
- Prompt quality, PDF parsing, and article-quality optimization
