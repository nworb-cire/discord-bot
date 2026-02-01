-- Reasons each book is or is not on the current (open) ballot.
-- Tweak params.max_appearances and params.is_staging to match your environment.
WITH
params AS (
    SELECT
        3::int AS max_appearances,
        false::bool AS is_staging
),
open_election AS (
    SELECT id, ballot
    FROM elections
    WHERE closed_at IS NULL
    ORDER BY opened_at DESC
    LIMIT 1
),
open_ballot AS (
    SELECT CAST(json_array_elements_text(open_election.ballot) AS int) AS book_id
    FROM open_election
),
ballot_size AS (
    SELECT COALESCE(json_array_length(open_election.ballot), 0) AS size
    FROM open_election
),
winner_books AS (
    SELECT winner AS book_id
    FROM elections
    WHERE winner IS NOT NULL
),
prior_ballot_entries AS (
    SELECT CAST(json_array_elements_text(e.ballot) AS int) AS book_id
    FROM elections e
    WHERE e.winner IS NOT NULL
),
appearance_totals AS (
    SELECT book_id, COUNT(*) AS appearance_count
    FROM prior_ballot_entries
    GROUP BY book_id
),
sub_votes AS (
    SELECT book_id, SUM(weight) AS vote_sum
    FROM votes
    GROUP BY book_id
),
nomination_stats AS (
    SELECT book_id, reactions
    FROM nominations
),
candidate_base AS (
    SELECT
        b.id AS book_id,
        b.title,
        b.created_at,
        COALESCE(n.reactions, 0) AS reactions,
        COALESCE(v.vote_sum, 0) AS vote_sum,
        COALESCE(n.reactions, 0) + COALESCE(v.vote_sum, 0) AS score,
        COALESCE(a.appearance_count, 0) AS appearance_count
    FROM books b
    LEFT JOIN nomination_stats n ON n.book_id = b.id
    LEFT JOIN sub_votes v ON v.book_id = b.id
    LEFT JOIN appearance_totals a ON a.book_id = b.id
    WHERE b.id NOT IN (SELECT book_id FROM winner_books)
      AND (
          (SELECT is_staging FROM params)
          OR COALESCE(n.reactions, 0) > 0
      )
      AND COALESCE(a.appearance_count, 0) < (SELECT max_appearances FROM params)
),
max_score AS (
    SELECT MAX(score) AS max_score
    FROM candidate_base
),
ranked_candidates AS (
    SELECT
        c.*, 
        ROW_NUMBER() OVER (
            ORDER BY
                (c.score <> m.max_score),
                (c.appearance_count > 0),
                c.score DESC,
                c.created_at ASC
        ) AS rank
    FROM candidate_base c
    CROSS JOIN max_score m
)
SELECT
    b.id AS book_id,
    b.title,
    (ob.book_id IS NOT NULL) AS on_ballot,
    CASE
        WHEN oe.id IS NULL THEN 'no_open_election'
        WHEN ob.book_id IS NOT NULL THEN 'on_ballot'
        WHEN wb.book_id IS NOT NULL THEN 'already_won'
        WHEN COALESCE(a.appearance_count, 0) >= (SELECT max_appearances FROM params)
            THEN 'max_appearances'
        WHEN NOT (SELECT is_staging FROM params)
             AND COALESCE(n.reactions, 0) <= 0
            THEN 'no_reactions'
        WHEN rc.rank IS NOT NULL AND rc.rank > (SELECT size FROM ballot_size)
            THEN 'below_cutoff'
        ELSE 'not_ranked'
    END AS reason,
    COALESCE(n.reactions, 0) AS reactions,
    COALESCE(v.vote_sum, 0) AS vote_sum,
    COALESCE(a.appearance_count, 0) AS prior_appearances,
    rc.rank AS candidate_rank,
    (SELECT size FROM ballot_size) AS ballot_size
FROM books b
LEFT JOIN open_election oe ON TRUE
LEFT JOIN open_ballot ob ON ob.book_id = b.id
LEFT JOIN winner_books wb ON wb.book_id = b.id
LEFT JOIN nomination_stats n ON n.book_id = b.id
LEFT JOIN sub_votes v ON v.book_id = b.id
LEFT JOIN appearance_totals a ON a.book_id = b.id
LEFT JOIN ranked_candidates rc ON rc.book_id = b.id
ORDER BY b.title;
