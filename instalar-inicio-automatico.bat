@echo off
chcp 65001 >nul
rem Cria um atalho na pasta Inicializar do Windows para o painel abrir junto com o computador.
set "ALVO=%~dp0iniciar.bat"
set "ATALHO=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Painel de Anuncios.lnk"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%ATALHO%');$s.TargetPath='%ALVO%';$s.WorkingDirectory='%~dp0';$s.WindowStyle=7;$s.Save()"
echo Pronto! O Painel de Anuncios vai iniciar automaticamente quando o Windows ligar.
echo Para desfazer, apague: %ATALHO%
pause
