import type { ApiErrorCategory, ParsedApiError } from '../api/error';
import type { UiLanguage } from '../i18n/uiText';

type ErrorText = readonly [title: string, message: string];

const requestFailed: ErrorText = ['Request failed', 'The request could not be completed. See the details for the service response.'];

const categories: Record<ApiErrorCategory, ErrorText> = {
  agent_disabled: ['Agent mode is off', 'Enable agent mode in settings, then try again.'],
  missing_params: ['Required input is missing', 'Enter a stock code and the other required fields.'],
  llm_not_configured: ['No model configured', 'Select a primary model and configure an enabled channel and API key in settings.'],
  model_tool_incompatible: ['Model does not support tools', 'Select a model that supports tool calls for this request.'],
  invalid_tool_call: ['Invalid model response', 'The model returned an incomplete tool call. Try another model or turn off its reasoning mode.'],
  portfolio_oversell: ['Sale exceeds available holdings', 'Correct the sale quantity or the corresponding transaction and try again.'],
  portfolio_busy: ['Portfolio ledger is busy', 'Another change is being processed. Try again in a moment.'],
  upstream_llm_400: ['Model provider rejected the request', 'Check the model name, request parameters and tool compatibility.'],
  upstream_timeout: ['External service timed out', 'Try again or check the server network and proxy settings.'],
  upstream_network: ['External service unavailable', 'The server could not reach its model or data provider. Check its network, DNS and proxy settings.'],
  local_connection_failed: ['Unable to reach the web service', 'Check that the service is running and that its address and port are accessible.'],
  http_error: requestFailed,
  unknown: requestFailed,
};

const errorCodes: Partial<Record<string, ErrorText>> = {
  validation_failed: ['Configuration needs attention', 'Check the channel API key, model and runtime settings.'],
  backend_removed: ['Chat backend removed', 'Select an available chat backend in settings.'],
  session_state_conflict: ['Conversation updated', 'Refresh the conversation and retry your question.'],
  screening_unavailable: ['Stock screener unavailable', 'Check the strategy configuration, data dependencies and service logs.'],
  screening_screen_task_not_found: ['Screening task unavailable', 'The server no longer has this task. Run the screen again.'],
  screening_screen_failed: ['Stock screening failed', 'The screener could not reach its market data or model provider. Try again.'],
};

export function localizedApiErrorText(error: ParsedApiError, language: UiLanguage) {
  if (language !== 'en') return error;
  const translated = (error.code && errorCodes[error.code]) || categories[error.category] || requestFailed;
  const hasChinese = /\p{Script=Han}/u;
  return {
    title: hasChinese.test(error.title) ? translated[0] : error.title,
    message: error.englishMessage || (hasChinese.test(error.message) ? translated[1] : error.message),
  };
}
