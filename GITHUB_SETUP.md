# Publish this project on GitHub

This archive is **prepared for GitHub**; it does not create or publish a repository on your behalf.

## 1. Create the repository

Visit <https://github.com/new> and create:

- **Repository name:** `MovieSortPro`
- **Visibility:** Public (so other people can see/use it)
- **Description:** `Windows movie library organiser with multi-source metadata and configurable folders`
- **Do not initialise** with a README, `.gitignore`, or licence: all three are in this project.

## 2. Publish from Windows

1. Install [Git for Windows](https://git-scm.com/download/win) if it is not already installed.
2. Extract the `MovieSortPro-GitHub-Ready.zip` archive.
3. Open **PowerShell** in the extracted `MovieSortPro-GitHub-Ready` folder.
4. Run (replacing `YOUR_USERNAME` with your own GitHub username):

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\Publish-To-GitHub.ps1 -Repository 'YOUR_USERNAME/MovieSortPro'
   ```

5. Sign in to GitHub when prompted by Git Credential Manager (if needed).
6. Open your public repository and confirm `README.md`, `.github/workflows/windows-build.yml` and `.py` files are visible.

**Alternative without Git CLI:** Use GitHub Desktop: add this extracted folder as a local repository, commit the files, and publish it as a **public** repository. If you use Desktop, do not create the repository separately through GitHub first.

## 3. Build and share a Windows EXE

Each push to `main` runs the Windows test/build workflow. On GitHub, open **Actions → Windows build and tests**, open the successful run, then download the `MovieSortPro-Windows` artifact.

For a permanent public **Releases** download, create and push a version tag:

```powershell
git tag v2.1.0
git push origin v2.1.0
```

The workflow will publish `MovieSortPro-Windows.zip` as the release asset for that version if tests and packaging pass. It may be useful to add a screenshot of the real Windows interface and a short demo to the README afterward.

## Keeping it updated

Edit code, test, then run:

```powershell
python -m unittest discover -v
git add .
git commit -m 'Describe your improvement'
git push
```

**Note:** GitHub Actions artifacts may expire. Use tagged Releases for stable downloads. The EXE is unsigned, and Windows may warn people before running it.
