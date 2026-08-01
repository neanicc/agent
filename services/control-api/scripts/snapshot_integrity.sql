SELECT jsonb_build_object(
  'max_cloud_ingest_seq',
    COALESCE((SELECT MAX(cloud_ingest_seq) FROM events), 0),
  'session_sequences',
    COALESCE((
      SELECT jsonb_object_agg(session_id::text, max_sequence)
      FROM (
        SELECT session_id, MAX(session_seq) AS max_sequence
        FROM events
        GROUP BY session_id
      ) AS stream_positions
    ), '{}'::jsonb),
  'artifact_hashes',
    COALESCE((
      SELECT jsonb_object_agg(id::text, sha256 ORDER BY id::text)
      FROM artifacts
      WHERE state = 'complete'
    ), '{}'::jsonb),
  'action_resolutions',
    COALESCE((
      SELECT jsonb_object_agg(action_id, resolution ORDER BY action_id)
      FROM actions
      WHERE resolution IS NOT NULL
    ), '{}'::jsonb)
)::text;
