import { mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
const [source, sample] = process.argv.slice(2);
const helper = join(source, 'packages/desktop/test/e2e/helpers/model-provider-restart-config.ts');
const { seedPersonalProviderConfig, seedPersonalDefaultModelSelection } = await import(pathToFileURL(helper).href);
const file = join(sample, '.zcode/v2/provider_config.json');
const model = 'zcode-local-demo';
await mkdir(dirname(file), { recursive: true });
await seedPersonalProviderConfig(file, {
  id: 'local-reference', label: '本地对照', apiFormat: 'openai-chat-completions',
  apiKey: 'local-reference-placeholder', baseURL: 'http://127.0.0.1:18764/v1',
  models: [
    { id: model, contextWindow: 128000, reasoningLevel: {values:['disabled','low','high','max'],map:'{}'} },
    { id: 'zcode-local-demo-fast', contextWindow: 64000 },
  ],
});
await seedPersonalDefaultModelSelection(file,{providerId:'local-reference', modelId:model, options:{reasoningLevel:'high'}});
console.log('ZCode local provider seeded');
