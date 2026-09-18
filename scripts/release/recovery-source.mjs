export function validateRecoverySource(run, jobs, tag) {
  if (run.path !== '.github/workflows/release.yml' || run.event !== 'push' || run.head_branch !== tag || run.status !== 'completed' || !/^[a-f0-9]{40}$/.test(run.head_sha || '')) throw new Error('Recovery requires a completed tag-triggered canonical release run');
  if (jobs.filter(job => job.name === 'Verify required release products' && job.conclusion === 'success').length !== 1) throw new Error('Required products were not verified');
  const publishers = jobs.filter(job => job.name === 'Publish one V8OS release');
  if (publishers.length !== 1 || !['failure', 'success'].includes(publishers[0].conclusion)) throw new Error('No completed publisher to recover');
  if (jobs.some(job => job.name !== 'Publish one V8OS release' && !['success', 'skipped'].includes(job.conclusion))) throw new Error('A build or verification failed; publication recovery is not allowed');
  return run.head_sha;
}
