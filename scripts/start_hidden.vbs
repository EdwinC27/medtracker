' ---------------------------------------------------------------------------
'  MedTracker - start without a console window.
'  Used by the auto-start scheduled task so the app runs quietly in the
'  background at logon. Stop it with scripts\stop.bat.
' ---------------------------------------------------------------------------
Dim shell, fso, projectRoot, python, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

projectRoot = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
python = projectRoot & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(python) Then
  python = projectRoot & "\.venv\Scripts\python.exe"
End If

If Not fso.FileExists(python) Then
  MsgBox "MedTracker: virtual environment not found. Run scripts\install.bat first.", 16, "MedTracker"
  WScript.Quit 1
End If

shell.CurrentDirectory = projectRoot
command = """" & python & """ -m app.main"
shell.Run command, 0, False
