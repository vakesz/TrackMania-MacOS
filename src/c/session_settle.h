/* The one case where session.c may skip its SETTLE_MS handoff window.
 * Kept free of Windows types so a host compiler can unit test the decision.
 *
 * The window exists because a launcher or loader can exit just before its
 * successor appears. Every handoff the job can see keeps ActiveProcesses above
 * zero: a child is placed in the job when it is created, which is before its
 * parent can exit, and the job sets no breakaway limit. So an empty job, an
 * exited root, an unchanged cumulative count and no unrecognized process
 * together leave nothing that could still start a watched process from inside
 * the job. Anything weaker than all of these keeps the full window. */
#ifndef TMNF_SESSION_SETTLE_H
#define TMNF_SESSION_SETTLE_H

typedef struct settle_facts
{
    int root_known, root_exited;
    unsigned long live_targets, live_job, ambiguous;
    /* The tick's reconcile result: cumulative and active counts matched. */
    int accounted;
    /* A fresh JobObjectBasicAccountingInformation read, taken after the tick's
     * snapshots. A failed query must keep the full window. */
    int job_query_ok;
    unsigned long active_processes, total_processes, known_job;
} SettleFacts;

static inline int settle_may_end_early(const SettleFacts *facts)
{
    return facts->root_known && facts->root_exited && !facts->live_targets && !facts->live_job &&
            facts->accounted && !facts->ambiguous && facts->job_query_ok &&
            !facts->active_processes && facts->total_processes == facts->known_job;
}

#endif
