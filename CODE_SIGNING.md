# Política de firma de código

*Code signing policy* de PDF Studio (los textos clave también en inglés, como pide SignPath).

> **Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by
> [SignPath Foundation](https://signpath.org/).**
>
> Firma de código gratuita provista por SignPath.io, certificado de SignPath Foundation.

**Estado:** solicitud en trámite. Hasta que SignPath Foundation apruebe el proyecto, los
ejecutables publicados **no están firmados** (el paso de firma del workflow está desactivado).

## Qué se firma

Solo lo que arma GitHub Actions a partir del código de este repositorio, al crear un tag
`v*` (workflow [`.github/workflows/release.yml`](.github/workflows/release.yml)):

| Archivo | Firma |
|---|---|
| `PDFStudio/PDFStudio.exe` (la aplicación; el mismo .exe corre sus tareas auxiliares) | Authenticode |
| `instalar.ps1`, `desinstalar.ps1` (instalador para el usuario actual) | Authenticode |

La configuración de SignPath está en [`.signpath/artifact-configuration.xml`](.signpath/artifact-configuration.xml).
Nunca se firman binarios armados en una computadora personal ni archivos de terceros
sueltos: las bibliotecas incluidas (Qt, MuPDF, OpenCV…) van dentro de la carpeta del
programa tal como las publican sus autores.

El armado es reproducible: Python, paquetes (`requirements-empaquetado.txt`) y acciones
de GitHub con versión fija (las acciones, por SHA de commit), `PYTHONHASHSEED=0` y la fecha
de los archivos del ZIP igual a la del commit (`SOURCE_DATE_EPOCH`). Cada release publica
`SHA256SUMS.txt`.

## Roles / Team roles

| Rol | Quién |
|---|---|
| Committers and reviewers (autores y revisores) | [@demian-luda](https://github.com/demian-luda) |
| Approvers (aprueban cada firma en SignPath) | [@demian-luda](https://github.com/demian-luda) |

Todo cambio entra por la rama `main` del repositorio; las contribuciones de terceros entran
por *pull request* revisado por un committer. Cada pedido de firma lo aprueba a mano un
approver en SignPath, después de verificar que corresponde a un tag publicado de este
repositorio.

## Autenticación en dos pasos (obligatoria)

Todas las personas con permiso de escritura en el repositorio o acceso al proyecto en
SignPath **tienen que tener activada la autenticación en dos pasos (2FA)**, tanto en
GitHub como en SignPath. Quien no la tenga no recibe esos permisos. El token de SignPath
(`SIGNPATH_API_TOKEN`) se guarda solo como secreto de GitHub Actions.

*Multi-factor authentication is required for all team members, on GitHub and on SignPath.*

## Privacidad / Privacy policy

*This program will not transfer any information to other networked systems unless
specifically requested by the user or the person installing or operating it.*

PDF Studio trabaja sin conexión: los documentos se abren, editan, reconocen (OCR) y firman
en la computadora. Solo se conecta a internet cuando el usuario lo pide y lo confirma, para
descargar una dependencia (una fuente libre, los idiomas de Tesseract, Ghostscript); antes
muestra qué se descarga, de dónde, su licencia y su tamaño. No tiene telemetría ni
actualizaciones automáticas. La validación de firmas digitales no consulta servidores
(no verifica la revocación de certificados en línea).

## Cómo se activa la firma (para los mantenedores)

1. SignPath Foundation aprueba el proyecto y crea el proyecto en SignPath con:
   la política de firma (*signing policy*) y la configuración de artefacto de
   `.signpath/artifact-configuration.xml`.
2. En GitHub → *Settings → Secrets and variables → Actions*:
   - **Secreto** `SIGNPATH_API_TOKEN`: token de API de un usuario de CI de SignPath
     (*submitter* en la política de firma).
   - **Variables**:

     | Variable | Valor |
     |---|---|
     | `SIGNPATH_HABILITADO` | `true` (con cualquier otro valor la firma se saltea) |
     | `SIGNPATH_ORGANIZATION_ID` | ID de la organización en SignPath |
     | `SIGNPATH_PROJECT_SLUG` | slug del proyecto (p. ej. `pdf-studio`) |
     | `SIGNPATH_SIGNING_POLICY_SLUG` | slug de la política (p. ej. `release-signing`) |
     | `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | slug de la configuración de artefacto (p. ej. `initial`) |
3. En SignPath se conecta el repositorio como *trusted build system* (GitHub Actions),
   para que solo se acepten artefactos armados por este workflow.
4. Al crear un tag `v*`, el workflow arma, sube la carpeta sin firmar, pide la firma, espera
   la aprobación, comprueba la firma del .exe (`Get-AuthenticodeSignature`) y publica el ZIP
   firmado con su `SHA256SUMS.txt`.

## Reportar un problema

Si un archivo firmado con este certificado no salió de este repositorio o tiene un
comportamiento malicioso, abrí un *issue* en
<https://github.com/demian-luda/pdf-studio/issues> o escribí a SignPath Foundation
(<https://signpath.org/>).
