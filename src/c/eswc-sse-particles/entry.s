# Loop heads of the SSE particle loop (billboards.c) for TmNationsESWC.exe 1.8.0.
#
# The patch writes a 5-byte jmp at each loop head of function 0x80ff40:
#   0x810685 (particles in depth order) -> offset 0x00 here
#   0x810d17 (particles in array order) -> offset 0x20 here
# Each is entered with ESP at the game function's frame, the x87 stack empty and
# every general register and flag dead. When the C loop is done, jump to the
# function's exit 0x811b0f; otherwise replay the bytes the jmp replaced and continue
# in the original loop body, whose back edge comes here again for the next particle.
#
# Assembled on its own, without a linker: SECTION_VA (the address of offset 0, given
# by trackmania_mac.build.sse_particles_builder) turns the jumps into the game into
# plain relative jumps, so the code needs no relocations.

	.intel_syntax noprefix
	.text
start:

	.macro	jmp_to address
	.byte	0xe9
	.long	\address - (SECTION_VA + (. + 4 - start))
	.endm

	push	esp				# argument: the frame (ESP before the push)
	call	_indexed_loop
	add	esp, 4
	test	eax, eax
	jnz	1f
	jmp_to	0x811b0f
1:	mov	edx, dword ptr [esp + 0x7c]	# replaced by the jmp at 0x810685
	mov	eax, dword ptr [esp + 0x18]
	jmp_to	0x81068d

	.org	0x20, 0xcc
	push	esp
	call	_linear_loop
	add	esp, 4
	test	eax, eax
	jnz	2f
	jmp_to	0x811b0f
2:	mov	ecx, dword ptr [esp + 0x98]	# replaced by the jmp at 0x810d17
	jmp_to	0x810d1e

	.org	0x40, 0xcc
	.include "billboards.s"
