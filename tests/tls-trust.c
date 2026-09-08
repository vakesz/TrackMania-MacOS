/* Test-only ROOT importer. Never run against a user or game prefix. */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <wincrypt.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv)
{
    HANDLE marker = INVALID_HANDLE_VALUE, file = INVALID_HANDLE_VALUE;
    HCERTSTORE store = NULL;
    PCCERT_CONTEXT certificate = NULL;
    PCERT_EXTENSION extension;
    CERT_BASIC_CONSTRAINTS2_INFO constraints;
    BY_HANDLE_FILE_INFORMATION information;
    LARGE_INTEGER length;
    char environment[129], proof[129], pem[65536], subject[128], fingerprint[65];
    BYTE der[65536], hash[32], usage[2];
    DWORD count, der_size = sizeof(der), hash_size = sizeof(hash), size;
    size_t nonce_length;
    unsigned int i;
    int result = 2;
    const char *failure = "disposable-prefix proof";

    if (argc != 4)
    {
        fputs("Usage: tls-trust.exe CERT_PEM NONCE EXPECTED_SHA256\n", stderr);
        return 2;
    }
    nonce_length = strlen(argv[2]);
    if (nonce_length < 32 || nonce_length > 128 ||
            strspn(argv[2], "0123456789abcdefABCDEF") != nonce_length ||
            GetEnvironmentVariableA("MACTMNF_DISPOSABLE_TEST", environment, sizeof(environment)) != nonce_length ||
            strcmp(environment, argv[2])) goto done;

    /* Hold the exact C: marker without write/delete sharing through the import. */
    marker = CreateFileW(L"C:\\mactmnf-test-prefix.txt", GENERIC_READ, FILE_SHARE_READ,
            NULL, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (marker == INVALID_HANDLE_VALUE || GetFileType(marker) != FILE_TYPE_DISK ||
            !GetFileInformationByHandle(marker, &information) ||
            (information.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) ||
            !GetFileSizeEx(marker, &length) || length.QuadPart != (LONGLONG)nonce_length ||
            !ReadFile(marker, proof, sizeof(proof), &count, NULL) || count != nonce_length ||
            memcmp(proof, argv[2], nonce_length)) goto done;

    result = 3;
    failure = "pinned test CA";
    if (strlen(argv[3]) != 64 || strspn(argv[3], "0123456789abcdefABCDEF") != 64) goto done;
    file = CreateFileA(argv[1], GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (file == INVALID_HANDLE_VALUE || GetFileType(file) != FILE_TYPE_DISK ||
            !GetFileSizeEx(file, &length) || length.QuadPart <= 0 || length.QuadPart > (LONGLONG)sizeof(pem) ||
            !ReadFile(file, pem, sizeof(pem), &count, NULL) || count != length.QuadPart ||
            !CryptStringToBinaryA(pem, count, CRYPT_STRING_BASE64HEADER,
                    der, &der_size, NULL, NULL) ||
            !CryptHashCertificate2(L"SHA256", 0, NULL, der, der_size, hash, &hash_size) ||
            hash_size != sizeof(hash)) goto done;
    for (i = 0; i < sizeof(hash); ++i) sprintf(fingerprint + 2 * i, "%02x", hash[i]);
    if (_stricmp(fingerprint, argv[3])) goto done;

    certificate = CertCreateCertificateContext(X509_ASN_ENCODING, der, der_size);
    if (!certificate || !CertGetNameStringA(certificate, CERT_NAME_ATTR_TYPE, 0,
            (void *)szOID_COMMON_NAME, subject, sizeof(subject)) ||
            strcmp(subject, "MacTMNF PUBLIC TEST ONLY ca") ||
            !CertCompareCertificateName(X509_ASN_ENCODING, &certificate->pCertInfo->Subject,
                    &certificate->pCertInfo->Issuer)) goto done;
    extension = CertFindExtension(szOID_BASIC_CONSTRAINTS2, certificate->pCertInfo->cExtension,
            certificate->pCertInfo->rgExtension);
    size = sizeof(constraints);
    if (!extension || !CryptDecodeObjectEx(X509_ASN_ENCODING, X509_BASIC_CONSTRAINTS2,
            extension->Value.pbData, extension->Value.cbData, 0, NULL, &constraints, &size) ||
            !constraints.fCA || !constraints.fPathLenConstraint || constraints.dwPathLenConstraint != 0 ||
            !CertGetIntendedKeyUsage(X509_ASN_ENCODING, certificate->pCertInfo, usage, sizeof(usage)) ||
            !(usage[0] & CERT_KEY_CERT_SIGN_KEY_USAGE) ||
            !CryptVerifyCertificateSignatureEx(0, X509_ASN_ENCODING, CRYPT_VERIFY_CERT_SIGN_SUBJECT_CERT,
                    (void *)certificate, CRYPT_VERIFY_CERT_SIGN_ISSUER_CERT,
                    (void *)certificate, 0, NULL)) goto done;

    /* No store API is reached until both ownership proof and CA validation pass. */
    puts("PASS: disposable-prefix proof and pinned test CA validated; opening CurrentUser ROOT");
    result = 1;
    failure = "CurrentUser ROOT import";
    store = CertOpenStore(CERT_STORE_PROV_SYSTEM_W, 0, 0, CERT_SYSTEM_STORE_CURRENT_USER, L"ROOT");
    if (!store || !CertAddEncodedCertificateToStore(store, X509_ASN_ENCODING, der, der_size,
            CERT_STORE_ADD_NEW, NULL)) goto done;
    result = 0;
    puts("PASS: pinned test CA imported into disposable CurrentUser ROOT");
done:
    if (result) fprintf(stderr, "REFUSE: %s (error=%lu)\n", failure, GetLastError());
    if (store) CertCloseStore(store, 0);
    if (certificate) CertFreeCertificateContext(certificate);
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (marker != INVALID_HANDLE_VALUE) CloseHandle(marker);
    return result;
}
