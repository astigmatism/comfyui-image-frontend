// Offline only: checks publication rules and graph links without queueing work.
import { readFile } from "node:fs/promises";
import { validatePublication, derivePublishedMetadata } from "../../comfyui_extension/comfyui-image-frontend-interface/web/publication.js";

const [workflowPath, apiPath, objectInfoPath] = process.argv.slice(2);
const [workflow, api, nodeDefs] = await Promise.all([workflowPath, apiPath, objectInfoPath].map(async (path) => JSON.parse(await readFile(path, "utf8"))));
const validation = validatePublication(workflow, api, nodeDefs);
const links = new Map(workflow.links.map((link) => [link[0], link]));
const nodes = new Map(workflow.nodes.map((node) => [node.id, node]));
for (const [id, from, slot, to, input] of links.values()) {
  if (!nodes.get(from)?.outputs?.[slot]?.links?.includes(id) || nodes.get(to)?.inputs?.[input]?.link !== id) {
    validation.errors.push(`Editable graph link ${id} has inconsistent endpoints`);
  }
}
for (const node of workflow.nodes) {
  for (const input of node.inputs ?? []) if (input.link != null && !links.has(input.link)) validation.errors.push(`Node ${node.id} has a dangling input link`);
  for (const output of node.outputs ?? []) for (const id of output.links ?? []) if (!links.has(id)) validation.errors.push(`Node ${node.id} has a dangling output link`);
}
const missing = [...new Set(Object.values(api).map((node) => node.class_type))].filter((name) => !nodeDefs[name]);
validation.errors.push(...missing.map((name) => `Missing node definition: ${name}`));
const metadata = derivePublishedMetadata(workflow, api, validation);
console.log(JSON.stringify({
  errors: validation.errors, warnings: validation.warnings,
  inputs: validation.inputs.map(({ bindings, instance_uuid, ...input }) => input),
  outputs: validation.outputs,
  loras: metadata.technical_inventory.loras,
  node_counts: metadata.technical_inventory.node_counts,
}, null, 2));
if (validation.errors.length) process.exitCode = 1;
