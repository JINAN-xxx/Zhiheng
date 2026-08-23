export function normalizeStoredTreeSelection(tree, storedKeys, linked) {
  const keys = [...new Set(Array.isArray(storedKeys) ? storedKeys : [])]
  if (!linked) return { checkedKeys: keys, halfCheckedKeys: [] }

  const branchKeys = new Set()
  const visit = (nodes) => {
    for (const node of Array.isArray(nodes) ? nodes : []) {
      if (Array.isArray(node.children) && node.children.length) {
        branchKeys.add(node.id)
        visit(node.children)
      }
    }
  }
  visit(tree)

  return {
    checkedKeys: keys.filter((key) => !branchKeys.has(key)),
    halfCheckedKeys: keys.filter((key) => branchKeys.has(key)),
  }
}

export function convertTreeSelectionMode(tree, checkedKeys, halfCheckedKeys, linked) {
  const selectedKeys = [...new Set([...(checkedKeys || []), ...(halfCheckedKeys || [])])]
  if (!linked) return { checkedKeys: selectedKeys, halfCheckedKeys: [] }

  const selected = new Set(selectedKeys)
  const expanded = []
  const collectSubtree = (node) => {
    expanded.push(node.id)
    for (const child of Array.isArray(node.children) ? node.children : []) collectSubtree(child)
  }
  const visit = (nodes) => {
    for (const node of Array.isArray(nodes) ? nodes : []) {
      if (selected.has(node.id)) collectSubtree(node)
      else visit(node.children)
    }
  }
  visit(tree)
  const normalized = normalizeStoredTreeSelection(tree, expanded, true)
  const expandedSet = new Set(expanded)
  const ancestorKeys = []
  const ancestorSet = new Set()
  const collectAncestors = (nodes, ancestors = []) => {
    for (const node of Array.isArray(nodes) ? nodes : []) {
      if (expandedSet.has(node.id)) {
        for (const ancestor of ancestors) {
          if (!ancestorSet.has(ancestor)) {
            ancestorSet.add(ancestor)
            ancestorKeys.push(ancestor)
          }
        }
      }
      const nextAncestors = Array.isArray(node.children) && node.children.length
        ? [...ancestors, node.id]
        : ancestors
      collectAncestors(node.children, nextAncestors)
    }
  }
  collectAncestors(tree)
  return {
    checkedKeys: normalized.checkedKeys,
    halfCheckedKeys: [...new Set([...normalized.halfCheckedKeys, ...ancestorKeys])],
  }
}
