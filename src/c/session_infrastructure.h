/* Which processes session.c treats as known, and which parents vouch for the
 * prefix infrastructure it recognizes. Kept free of Windows types so a host
 * compiler can unit test the rules.
 *
 * Every process the session sees after its baseline is unrecognized unless it
 * is watched, the launch root, or verified infrastructure. An unrecognized
 * process makes the result uncertain, so cleanup never kills what may still
 * be a game. Infrastructure is recognized only from file identity, byte
 * equality with this runtime's own image and a retained parent:
 *   - console host: job member, started by the root or a watched process,
 *     recognized only after it exited with status zero;
 *   - desktop: job member, started by the root or a watched process,
 *     recognized while alive or after exit zero;
 *   - service: outside the job, a System32 Wine program started by the
 *     services.exe that was already running at the baseline (Wine starts
 *     lsass.exe this way on demand), recognized while alive. A process outside
 *     the job never counts once it has exited, so this exempts only live ones.
 *     A process inside the job is never a service. */
#ifndef TRACKMANIA_MAC_SESSION_INFRASTRUCTURE_H
#define TRACKMANIA_MAC_SESSION_INFRASTRUCTURE_H

typedef struct process_facts
{
    int watched, baseline, root, in_job, exited;
    unsigned long exit_code;
    /* The launch root is explorer's virtual desktop (--desktop). */
    int desktop_session;
    int console_verified, desktop_verified, service_verified;
} ProcessFacts;

typedef struct parent_facts
{
    int in_job, root, watched, baseline, service_manager;
    /* The parent was created no later than the child. */
    int created_first;
} ParentFacts;

static inline int process_is_unrecognized(const ProcessFacts *p)
{
    int clean = !p->exited || !p->exit_code;
    if (p->watched || p->baseline) return 0;
    if (p->desktop_verified && clean) return 0;
    if (p->service_verified && !p->in_job && clean) return 0;
    return (!p->root && p->in_job && !p->console_verified) ||
            (!p->exited && !(p->desktop_session && p->root));
}

/* service: whether the child is a service candidate rather than a console
 * host or desktop candidate. */
static inline int infrastructure_parent(int service, const ParentFacts *parent)
{
    if (!parent->created_first) return 0;
    if (service) return parent->baseline && parent->service_manager && !parent->in_job;
    return parent->in_job && (parent->root || parent->watched);
}

#endif
