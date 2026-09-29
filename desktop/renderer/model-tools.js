
'use strict';
let modelRequestBusy = false;
function initializeModelTools() {
  $('deepseek-preset').addEventListener('click', () => {
    const form = $('settings-form');
    for (const [name,value] of Object.entries({provider:'openai',base_url:'https://api.deepseek.com',model:'deepseek-flash',api_path:'',thinking_mode:'disabled',reasoning_effort:'high'})) form.elements.namedItem(name).value = value;
    $('model-probe-result').textContent = 'DeepSeek 配置已填入，尚未保存。请填写密钥并保存后再测试。';
    form.elements.namedItem('api_key').focus();
  });
  $('model-probe').addEventListener('click', () => runModelTool('model_probe'));
  $('model-catalog').addEventListener('click', () => runModelTool('model_catalog'));
}
async function runModelTool(method) {
  if (modelRequestBusy || busy || running()) return;
  modelRequestBusy = true; $('model-probe').disabled = $('model-catalog').disabled = true;
  $('model-probe-result').textContent = method === 'model_probe' ? '正在测试已保存连接…' : '正在获取已保存端点的模型列表…';
  try {
    const result = await api.request(method);
    renderModelToolResult(result, method);
  } catch (error) { $('model-probe-result').textContent = `请求失败：${error.message}。请检查已保存的端点、模型和密钥。`; }
  finally { modelRequestBusy = false; $('model-probe').disabled = $('model-catalog').disabled = false; }
}
function renderModelToolResult(result, method) {
  const duration = result.latency_ms ?? result.elapsed_ms ?? result.duration_ms, model = result.model || state.config.model || '未提供模型名称';
  $('model-probe-result').textContent = `${result.ok === false ? '失败' : method === 'model_probe' ? '连接测试通过' : '模型列表已获取'} · ${model}${duration != null ? ` · ${number(duration)} 毫秒` : ''}${result.error ? ` · ${result.error}` : ''}`;
  if (method !== 'model_catalog') {
    if (result.usage?.total_tokens != null) $('model-probe-result').textContent += ` · 用量 ${number(result.usage.total_tokens)} token`;
    return;
  }
  const list = $('model-catalog-list'); list.replaceChildren();
  for (const item of result.models || []) {
    const name = typeof item === 'string' ? item : item.id || item.name;
    if (!name) continue;
    const button = element('button','model-option',name);
    button.addEventListener('click', () => { $('settings-form').elements.namedItem('model').value = name; $('model-probe-result').textContent = `已选择 ${name}，请保存后使用。`; }); list.append(button);
  }
  if (!list.childElementCount && result.ok !== false) list.append(element('p','field-hint','端点没有返回可选模型。可手动填写模型名称。'));
}
