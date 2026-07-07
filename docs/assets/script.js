
document.querySelectorAll('.row').forEach(function (row) {
  row.addEventListener('click', function () {
    var detail = row.querySelector('.detail');
    if (detail) { detail.classList.toggle('open'); }
  });
});
