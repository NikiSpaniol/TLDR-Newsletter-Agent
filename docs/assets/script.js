
document.querySelectorAll('.card').forEach(function (card) {
  card.addEventListener('click', function () {
    var detail = card.querySelector('.detail');
    if (detail) { detail.classList.toggle('open'); }
  });
});
