create table if not exists quality_reports (
  repo text not null, commit text not null, branch text not null,
  generated_at timestamptz not null, ci_system text not null,
  dup_percent numeric, dup_lines int, dup_clones int,
  dead_total int, dead_by_kind jsonb,
  cc_threshold int, cc_over int, cc_over_by jsonb, cc_max int, cc_p95 numeric, cc_avg numeric,
  nloc int, functions int,
  ratchet_result text, ratchet_violations jsonb,
  raw jsonb not null,
  primary key (repo, commit, branch)
);

create table if not exists hotspots (
  repo text not null, week date not null, file text not null,
  churn int not null, fix_commits int,
  complexity int not null, nloc int not null,
  score numeric not null, rank int not null,
  primary key (repo, week, file)
);
