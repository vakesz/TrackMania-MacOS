	.file	"billboards.c"
	.intel_syntax noprefix
	.text
	.p2align 4
	.globl	"_indexed_loop"
	.def	"_indexed_loop";	.scl	2;	.type	32;	.endef
"_indexed_loop":
	push	ebp
	mov	ebp, esp
	push	edi
	push	esi
	mov	esi, 1
	push	ebx
	and	esp, -16
	sub	esp, 64
	mov	ebx, DWORD PTR [ebp+8]
	test	BYTE PTR [ebx+345], 3
	jne	L1
	stmxcsr	DWORD PTR [esp+60]
	mov	DWORD PTR [esp+56], 32640
	mov	esi, DWORD PTR [esp+60]
	ldmxcsr	DWORD PTR [esp+56]
	mov	eax, DWORD PTR [ebx+120]
	mov	ecx, DWORD PTR [ebx+24]
	mov	edi, DWORD PTR [ebx+248]
	mov	DWORD PTR [esp+32], eax
	mov	eax, DWORD PTR [ebx+124]
	mov	DWORD PTR [esp+48], ecx
	lea	eax, [eax+ecx*4]
	mov	DWORD PTR [esp+44], eax
	mov	eax, -2147483648
	mov	DWORD PTR [esp+36], eax
	mov	eax, 2147483647
	mov	DWORD PTR [esp+40], eax
L27:
	mov	eax, DWORD PTR [esp+44]
	mov	edx, DWORD PTR [ebx+152]
	movups	xmm0, XMMWORD PTR [ebx+156]
	movups	xmm6, XMMWORD PTR [ebx+168]
	mov	eax, DWORD PTR [eax]
	mov	edx, DWORD PTR [edx+132]
	lea	eax, [eax+eax*4]
	lea	edx, [edx+eax*8]
	movups	xmm4, XMMWORD PTR [edx]
	movss	xmm7, DWORD PTR [edx+20]
	movaps	xmm5, xmm4
	shufps	xmm5, xmm4, 255
	mulps	xmm0, xmm5
	movaps	xmm3, xmm5
	mulss	xmm3, xmm7
	movaps	xmm2, xmm0
	movaps	xmm1, xmm3
	cmpunordps	xmm2, xmm0
	shufps	xmm1, xmm3, 0
	mulps	xmm1, xmm6
	movaps	XMMWORD PTR [esp+16], xmm2
	movaps	xmm2, xmm4
	cmpunordps	xmm2, xmm4
	orps	xmm2, XMMWORD PTR [esp+16]
	movaps	xmm6, xmm1
	cmpunordps	xmm6, xmm1
	orps	xmm2, xmm6
	movmskps	eax, xmm2
	test	al, 7
	jne	L56
	movd	xmm6, DWORD PTR [esp+40]
	mov	eax, 2139095039
	mov	ecx, 8388608
	pshufd	xmm2, xmm6, 0
	pxor	xmm6, xmm6
	andps	xmm3, xmm2
	comiss	xmm3, xmm6
	jp	L6
	jne	L6
	comiss	xmm5, xmm6
	jp	L35
	jne	L35
L8:
	movd	xmm7, eax
	pshufd	xmm5, xmm7, 0
	comiss	xmm3, xmm5
	jp	L36
	je	L56
L36:
	movaps	xmm3, xmm4
	addps	xmm3, xmm0
	movaps	xmm6, xmm3
	andps	xmm6, xmm2
	comiss	xmm6, xmm5
	jp	L37
	je	L56
L37:
	movd	xmm7, DWORD PTR [esp+36]
	xor	eax, eax
	pshufd	xmm6, xmm7, 0
	xorps	xmm0, xmm6
	addps	xmm0, xmm4
	andps	xmm2, xmm0
	comiss	xmm2, xmm5
	jp	L13
	sete	al
L13:
	mov	DWORD PTR [esp+12], eax
	test	eax, eax
	jne	L56
	movups	xmm4, XMMWORD PTR [edx+24]
	mov	eax, 1132396544
	mov	edx, DWORD PTR [edx+16]
	movd	xmm7, eax
	mov	eax, 1191182336
	pshufd	xmm2, xmm7, 0
	mulps	xmm4, xmm2
	movd	xmm7, eax
	mov	eax, -956301056
	pshufd	xmm2, xmm7, 0
	movaps	xmm5, xmm4
	movd	xmm7, eax
	mov	eax, 32768
	cmpltps	xmm5, xmm2
	pshufd	xmm2, xmm7, 0
	cmpltps	xmm2, xmm4
	cvttps2dq	xmm4, xmm4
	movd	xmm7, eax
	andps	xmm2, xmm5
	pshufd	xmm7, xmm7, 0
	movdqa	xmm5, xmm2
	pand	xmm2, xmm4
	pandn	xmm5, xmm7
	por	xmm2, xmm5
	pshufd	xmm4, xmm2, 170
	movd	DWORD PTR [esp+8], xmm2
	movd	DWORD PTR [esp+16], xmm4
	pshufd	xmm4, xmm2, 85
	pshufd	xmm2, xmm2, 255
	movd	ecx, xmm4
	movd	eax, xmm2
	sal	eax, 16
	movzx	ecx, cx
	or	ecx, eax
	movzx	eax, WORD PTR [esp+16]
	sal	ecx, 8
	mov	DWORD PTR [esp+4], ecx
	mov	ecx, DWORD PTR [esp+8]
	sal	ecx, 16
	or	eax, ecx
	mov	ecx, DWORD PTR [esp+4]
	or	ecx, eax
	mov	eax, edx
	mov	DWORD PTR [esp], ecx
	mov	ecx, DWORD PTR [ebx+252]
	sal	eax, 4
	cmp	edx, DWORD PTR [ecx+4]
	cmovnb	eax, DWORD PTR [esp+12]
	add	eax, DWORD PTR [ecx+8]
	mov	ecx, DWORD PTR [eax]
	mov	edx, ecx
	mov	DWORD PTR [esp+8], ecx
	and	edx, 2143289344
	cmp	edx, 2139095040
	jne	L16
	mov	edx, ecx
	or	edx, 4194304
	test	ecx, 4194303
	cmove	edx, ecx
	mov	DWORD PTR [esp+8], edx
L16:
	mov	edx, DWORD PTR [eax+4]
	mov	ecx, edx
	mov	DWORD PTR [esp+16], edx
	and	ecx, 2143289344
	cmp	ecx, 2139095040
	jne	L19
	mov	ecx, edx
	or	ecx, 4194304
	test	edx, 4194303
	cmove	ecx, edx
	mov	DWORD PTR [esp+16], ecx
L19:
	mov	edx, DWORD PTR [eax+8]
	mov	ecx, edx
	mov	DWORD PTR [esp+4], edx
	and	ecx, 2143289344
	cmp	ecx, 2139095040
	jne	L22
	mov	ecx, edx
	or	ecx, 4194304
	test	edx, 4194303
	cmove	ecx, edx
	mov	DWORD PTR [esp+4], ecx
L22:
	mov	eax, DWORD PTR [eax+12]
	mov	ecx, eax
	and	ecx, 2143289344
	cmp	ecx, 2139095040
	jne	L25
	mov	ecx, eax
	or	ecx, 4194304
	test	eax, 4194303
	cmovne	eax, ecx
L25:
	movaps	xmm2, xmm1
	xorps	xmm6, xmm1
	mov	DWORD PTR [edi+68], eax
	add	edi, 96
	addps	xmm2, xmm0
	addps	xmm0, xmm6
	mov	DWORD PTR [edi-4], eax
	addps	xmm1, xmm3
	addps	xmm3, xmm6
	movlps	QWORD PTR [edi-96], xmm2
	mov	ecx, DWORD PTR [esp+8]
	mov	edx, DWORD PTR [esp]
	movhlps	xmm2, xmm2
	movss	DWORD PTR [edi-88], xmm2
	mov	DWORD PTR [edi-80], ecx
	mov	ecx, DWORD PTR [esp+16]
	mov	DWORD PTR [edi-84], edx
	movlps	QWORD PTR [edi-72], xmm0
	movhlps	xmm0, xmm0
	mov	DWORD PTR [edi-76], ecx
	mov	ecx, edx
	mov	DWORD PTR [edi-60], edx
	mov	edx, DWORD PTR [esp+4]
	mov	DWORD PTR [edi-36], ecx
	mov	DWORD PTR [edi-56], edx
	mov	edx, DWORD PTR [esp+16]
	movlps	QWORD PTR [edi-48], xmm1
	movhlps	xmm1, xmm1
	mov	DWORD PTR [edi-52], edx
	mov	edx, ecx
	mov	ecx, DWORD PTR [esp+8]
	mov	DWORD PTR [edi-12], edx
	movlps	QWORD PTR [edi-24], xmm3
	mov	edx, DWORD PTR [esp+4]
	movhlps	xmm3, xmm3
	add	DWORD PTR [esp+48], 1
	mov	eax, DWORD PTR [esp+48]
	movss	DWORD PTR [edi-64], xmm0
	movss	DWORD PTR [edi-40], xmm1
	mov	DWORD PTR [edi-32], ecx
	movss	DWORD PTR [edi-16], xmm3
	mov	DWORD PTR [edi-8], edx
	add	DWORD PTR [esp+44], 4
	cmp	eax, DWORD PTR [esp+32]
	jb	L27
	mov	edx, esi
	mov	esi, DWORD PTR [esp+12]
	jmp	L4
	.p2align 4,,10
	.p2align 3
L6:
	movd	xmm7, ecx
	pshufd	xmm5, xmm7, 0
	comiss	xmm5, xmm3
	jbe	L8
	.p2align 4
	.p2align 3
L56:
	mov	edx, esi
	mov	esi, 1
L4:
	mov	eax, DWORD PTR [esp+48]
	mov	DWORD PTR [ebx+248], edi
	mov	DWORD PTR [ebx+24], eax
	mov	DWORD PTR [esp+52], edx
	ldmxcsr	DWORD PTR [esp+52]
L1:
	lea	esp, [ebp-12]
	mov	eax, esi
	pop	ebx
	pop	esi
	pop	edi
	pop	ebp
	ret
	.p2align 4,,10
	.p2align 3
L35:
	pxor	xmm5, xmm5
	comiss	xmm7, xmm5
	jp	L56
	je	L8
	jmp	L56
	.p2align 4
	.globl	"_linear_loop"
	.def	"_linear_loop";	.scl	2;	.type	32;	.endef
"_linear_loop":
	push	ebp
	mov	ebp, esp
	push	edi
	push	esi
	mov	esi, 1
	push	ebx
	and	esp, -16
	sub	esp, 64
	mov	ebx, DWORD PTR [ebp+8]
	test	BYTE PTR [ebx+345], 3
	jne	L60
	stmxcsr	DWORD PTR [esp+60]
	mov	DWORD PTR [esp+56], 32640
	mov	esi, DWORD PTR [esp+60]
	ldmxcsr	DWORD PTR [esp+56]
	mov	eax, DWORD PTR [ebx+28]
	mov	edi, DWORD PTR [ebx+248]
	mov	DWORD PTR [esp+48], eax
	mov	eax, DWORD PTR [ebx+124]
	mov	DWORD PTR [esp+28], eax
	mov	eax, -2147483648
	mov	DWORD PTR [esp+20], eax
	mov	eax, 2147483647
	mov	DWORD PTR [esp+24], eax
L86:
	mov	eax, DWORD PTR [ebx+152]
	mov	edx, DWORD PTR [esp+48]
	movups	xmm0, XMMWORD PTR [ebx+156]
	movups	xmm6, XMMWORD PTR [ebx+168]
	add	edx, DWORD PTR [eax+132]
	movups	xmm4, XMMWORD PTR [edx]
	movss	xmm7, DWORD PTR [edx+20]
	movaps	xmm5, xmm4
	shufps	xmm5, xmm4, 255
	mulps	xmm0, xmm5
	movaps	xmm3, xmm5
	mulss	xmm3, xmm7
	movaps	xmm2, xmm0
	movaps	xmm1, xmm3
	cmpunordps	xmm2, xmm0
	shufps	xmm1, xmm3, 0
	mulps	xmm1, xmm6
	movaps	XMMWORD PTR [esp+32], xmm2
	movaps	xmm2, xmm4
	cmpunordps	xmm2, xmm4
	orps	xmm2, XMMWORD PTR [esp+32]
	movaps	xmm6, xmm1
	cmpunordps	xmm6, xmm1
	orps	xmm2, xmm6
	movmskps	eax, xmm2
	test	al, 7
	jne	L115
	movd	xmm6, DWORD PTR [esp+24]
	mov	ecx, 2139095039
	mov	eax, 8388608
	pshufd	xmm2, xmm6, 0
	pxor	xmm6, xmm6
	andps	xmm3, xmm2
	comiss	xmm3, xmm6
	jp	L65
	jne	L65
	comiss	xmm5, xmm6
	jp	L94
	jne	L94
L67:
	movd	xmm7, ecx
	pshufd	xmm5, xmm7, 0
	comiss	xmm3, xmm5
	jp	L95
	je	L115
L95:
	movaps	xmm3, xmm4
	addps	xmm3, xmm0
	movaps	xmm6, xmm3
	andps	xmm6, xmm2
	comiss	xmm6, xmm5
	jp	L96
	je	L115
L96:
	movd	xmm7, DWORD PTR [esp+20]
	xor	eax, eax
	pshufd	xmm6, xmm7, 0
	xorps	xmm0, xmm6
	addps	xmm0, xmm4
	andps	xmm2, xmm0
	comiss	xmm2, xmm5
	jp	L72
	sete	al
L72:
	mov	DWORD PTR [esp+16], eax
	test	eax, eax
	jne	L115
	movups	xmm4, XMMWORD PTR [edx+24]
	mov	eax, 1132396544
	mov	edx, DWORD PTR [edx+16]
	movd	xmm7, eax
	mov	eax, 1191182336
	pshufd	xmm2, xmm7, 0
	mulps	xmm4, xmm2
	movd	xmm7, eax
	mov	eax, -956301056
	pshufd	xmm2, xmm7, 0
	movaps	xmm5, xmm4
	movd	xmm7, eax
	mov	eax, 32768
	cmpltps	xmm5, xmm2
	pshufd	xmm2, xmm7, 0
	cmpltps	xmm2, xmm4
	cvttps2dq	xmm4, xmm4
	movd	xmm7, eax
	andps	xmm2, xmm5
	pshufd	xmm7, xmm7, 0
	movdqa	xmm5, xmm2
	pand	xmm2, xmm4
	pandn	xmm5, xmm7
	por	xmm2, xmm5
	pshufd	xmm4, xmm2, 170
	movd	DWORD PTR [esp+12], xmm2
	movd	DWORD PTR [esp+32], xmm4
	pshufd	xmm4, xmm2, 85
	pshufd	xmm2, xmm2, 255
	movd	ecx, xmm4
	movd	eax, xmm2
	sal	eax, 16
	movzx	ecx, cx
	or	ecx, eax
	movzx	eax, WORD PTR [esp+32]
	sal	ecx, 8
	mov	DWORD PTR [esp+8], ecx
	mov	ecx, DWORD PTR [esp+12]
	sal	ecx, 16
	or	eax, ecx
	mov	ecx, DWORD PTR [esp+8]
	or	ecx, eax
	mov	eax, edx
	mov	DWORD PTR [esp+4], ecx
	mov	ecx, DWORD PTR [ebx+252]
	sal	eax, 4
	cmp	edx, DWORD PTR [ecx+4]
	cmovnb	eax, DWORD PTR [esp+16]
	add	eax, DWORD PTR [ecx+8]
	mov	ecx, DWORD PTR [eax]
	mov	DWORD PTR [esp+32], eax
	mov	edx, ecx
	mov	DWORD PTR [esp+12], ecx
	and	edx, 2143289344
	cmp	edx, 2139095040
	jne	L75
	mov	edx, ecx
	or	edx, 4194304
	test	ecx, 4194303
	cmove	edx, ecx
	mov	DWORD PTR [esp+12], edx
L75:
	mov	eax, DWORD PTR [esp+32]
	mov	edx, DWORD PTR [eax+4]
	mov	ecx, edx
	and	ecx, 2143289344
	cmp	ecx, 2139095040
	jne	L78
	mov	ecx, edx
	or	ecx, 4194304
	test	edx, 4194303
	cmovne	edx, ecx
L78:
	mov	eax, DWORD PTR [esp+32]
	mov	ecx, DWORD PTR [eax+8]
	mov	DWORD PTR [esp+8], ecx
	and	ecx, 2143289344
	cmp	ecx, 2139095040
	jne	L81
	mov	eax, DWORD PTR [esp+8]
	mov	ecx, eax
	or	ecx, 4194304
	test	eax, 4194303
	mov	eax, ecx
	cmove	eax, DWORD PTR [esp+8]
	mov	DWORD PTR [esp+8], eax
L81:
	mov	eax, DWORD PTR [esp+32]
	mov	eax, DWORD PTR [eax+12]
	mov	ecx, eax
	mov	DWORD PTR [esp+32], eax
	and	ecx, 2143289344
	cmp	ecx, 2139095040
	jne	L84
	mov	ecx, eax
	or	ecx, 4194304
	test	eax, 4194303
	mov	eax, ecx
	cmove	eax, DWORD PTR [esp+32]
	mov	DWORD PTR [esp+32], eax
L84:
	movaps	xmm2, xmm1
	xorps	xmm6, xmm1
	addps	xmm1, xmm3
	mov	DWORD PTR [edi+20], edx
	addps	xmm2, xmm0
	addps	xmm0, xmm6
	mov	DWORD PTR [edi+44], edx
	add	edi, 96
	addps	xmm3, xmm6
	movlps	QWORD PTR [edi-96], xmm2
	mov	eax, DWORD PTR [esp+4]
	mov	ecx, DWORD PTR [esp+12]
	movhlps	xmm2, xmm2
	movlps	QWORD PTR [edi-72], xmm0
	movhlps	xmm0, xmm0
	mov	DWORD PTR [edi-80], ecx
	mov	ecx, eax
	mov	DWORD PTR [edi-84], eax
	mov	edx, ecx
	mov	DWORD PTR [edi-60], eax
	mov	eax, DWORD PTR [esp+8]
	mov	DWORD PTR [edi-36], ecx
	movlps	QWORD PTR [edi-48], xmm1
	mov	ecx, DWORD PTR [esp+12]
	movhlps	xmm1, xmm1
	mov	DWORD PTR [edi-56], eax
	mov	DWORD PTR [edi-32], ecx
	mov	ecx, DWORD PTR [esp+32]
	mov	DWORD PTR [edi-8], eax
	movlps	QWORD PTR [edi-24], xmm3
	mov	eax, DWORD PTR [esp+32]
	movhlps	xmm3, xmm3
	movss	DWORD PTR [edi-88], xmm2
	movss	DWORD PTR [edi-64], xmm0
	movss	DWORD PTR [edi-40], xmm1
	mov	DWORD PTR [edi-28], ecx
	movss	DWORD PTR [edi-16], xmm3
	mov	DWORD PTR [edi-12], edx
	mov	DWORD PTR [edi-4], eax
	add	DWORD PTR [esp+48], 40
	sub	DWORD PTR [esp+28], 1
	jne	L86
	mov	edx, esi
	mov	esi, DWORD PTR [esp+16]
	jmp	L63
	.p2align 4,,10
	.p2align 3
L65:
	movd	xmm7, eax
	pshufd	xmm5, xmm7, 0
	comiss	xmm5, xmm3
	jbe	L67
	.p2align 4
	.p2align 3
L115:
	mov	edx, esi
	mov	esi, 1
L63:
	mov	eax, DWORD PTR [esp+48]
	mov	DWORD PTR [ebx+248], edi
	mov	DWORD PTR [ebx+28], eax
	mov	eax, DWORD PTR [esp+28]
	mov	DWORD PTR [ebx+124], eax
	mov	DWORD PTR [esp+52], edx
	ldmxcsr	DWORD PTR [esp+52]
L60:
	lea	esp, [ebp-12]
	mov	eax, esi
	pop	ebx
	pop	esi
	pop	edi
	pop	ebp
	ret
	.p2align 4,,10
	.p2align 3
L94:
	pxor	xmm5, xmm5
	comiss	xmm7, xmm5
	jp	L115
	je	L67
	jmp	L115
