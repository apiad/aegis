- **A file picked on Android arrives with its bytes when the phone reports
  its size as 0.** Some document providers report no size for a file that
  has content, and the upload sent an empty file. The browser now reads such
  a file once and uploads what it holds.
