/** Pure graph validation only. No tools, authority, persistence, or execution. */
export function planDag(input) {
  if (!input || input.version !== 1 || typeof input.dag_id !== 'string' ||
      !/^[a-zA-Z0-9_-]{1,64}$/.test(input.dag_id) ||
      typeof input.goal !== 'string' || !input.goal.trim() ||
      !Array.isArray(input.nodes) || input.nodes.length < 1 || input.nodes.length > 100) {
    throw new Error('INVALID_DAG');
  }
  const nodes = input.nodes.map(node => {
    if (!node || typeof node.key !== 'string' ||
        !/^[a-zA-Z0-9_-]{1,64}$/.test(node.key) ||
        typeof node.prompt !== 'string' || !node.prompt.trim() ||
        !Array.isArray(node.depends_on) ||
        node.depends_on.some(key => typeof key !== 'string') ||
        new Set(node.depends_on).size !== node.depends_on.length ||
        !Array.isArray(node.acceptance_criteria) || !node.acceptance_criteria.length ||
        node.acceptance_criteria.some(value => typeof value !== 'string' || !value.trim())) {
      throw new Error('INVALID_NODE');
    }
    return {key: node.key, prompt: node.prompt, depends_on: [...node.depends_on],
      acceptance_criteria: [...node.acceptance_criteria]};
  });
  const keys = new Set(nodes.map(node => node.key));
  if (keys.size !== nodes.length) throw new Error('DUPLICATE_NODE');
  for (const node of nodes) {
    if (node.depends_on.some(key => !keys.has(key))) throw new Error('MISSING_DEPENDENCY');
    if (node.depends_on.includes(node.key)) throw new Error('SELF_DEPENDENCY');
  }
  const remaining = new Map(nodes.map(node => [node.key, node]));
  const done = new Set();
  const waves = [];
  while (remaining.size) {
    const ready = [...remaining.values()].filter(node =>
      node.depends_on.every(key => done.has(key))).map(node => node.key).sort();
    if (!ready.length) throw new Error('CYCLIC_DAG');
    // A wave completes before any dependent wave. Chunk by the original batch limit.
    waves.push(Array.from({length: Math.ceil(ready.length / 3)}, (_, i) => ready.slice(i * 3, i * 3 + 3)));
    ready.forEach(key => {remaining.delete(key); done.add(key);});
  }
  return {version: 1, dag_id: input.dag_id, goal: input.goal, nodes, waves,
    mode: 'work-coordinated', automatic_dispatch: false, host_dag_enforced: false};
}
